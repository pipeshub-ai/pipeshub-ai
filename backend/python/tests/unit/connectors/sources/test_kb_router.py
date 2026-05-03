"""Comprehensive tests for kb_router.py to maximize coverage.

Tests all route handler functions by using the FastAPI TestClient approach
with mocked dependencies (KnowledgeBaseService, KafkaService).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.connectors.sources.localKB.api.kb_router import (
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_MAX_STATUS,
    HTTP_MIN_STATUS,
    _parse_comma_separated_str,
    get_kb_service,
    get_kafka_service,
    kb_router,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mock_kb_service():
    svc = AsyncMock()
    svc.create_knowledge_base = AsyncMock()
    svc.list_user_knowledge_bases = AsyncMock()
    svc.get_knowledge_base = AsyncMock()
    svc.update_knowledge_base = AsyncMock()
    svc.delete_knowledge_base = AsyncMock()
    svc.create_records_in_kb = AsyncMock()
    svc.upload_records_to_kb = AsyncMock()
    svc.upload_records_to_folder = AsyncMock()
    svc.create_folder_in_kb = AsyncMock()
    svc.create_nested_folder = AsyncMock()
    svc.get_folder_contents = AsyncMock()
    svc.updateFolder = AsyncMock()
    svc.delete_folder = AsyncMock()
    svc.list_kb_records = AsyncMock()
    svc.get_kb_children = AsyncMock()
    svc.get_folder_children = AsyncMock()
    svc.create_kb_permissions = AsyncMock()
    svc.update_kb_permission = AsyncMock()
    svc.remove_kb_permission = AsyncMock()
    svc.list_kb_permissions = AsyncMock()
    svc.create_records_in_folder = AsyncMock()
    svc.update_record = AsyncMock()
    svc.delete_record = AsyncMock()
    return svc


def _mock_kafka_service():
    svc = AsyncMock()
    svc.publish_event = AsyncMock()
    return svc


def _build_app(kb_service=None, kafka_service=None):
    """Build a FastAPI app with the kb_router and mocked deps."""
    app = FastAPI()
    _kb = kb_service or _mock_kb_service()
    _kafka = kafka_service or _mock_kafka_service()

    # Mock out the auth dependency
    async def _noop_auth():
        pass

    # Override all dependencies
    app.dependency_overrides[get_kb_service] = lambda: _kb
    app.dependency_overrides[get_kafka_service] = lambda: _kafka

    # Attach container for routes that access request.app.container
    mock_container = MagicMock()
    mock_container.logger.return_value = MagicMock()
    mock_container.kafka_service.return_value = _kafka
    app.container = mock_container

    # Mock graph_provider
    app.state.graph_provider = MagicMock()

    # Include the router
    app.include_router(kb_router)

    return app, _kb, _kafka


def _mock_request_state():
    """Middleware-injected user state."""
    return {"userId": "user-1", "orgId": "org-1"}


# We need a middleware to inject request.state.user
class _InjectUserMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Inject user state into request
            scope.setdefault("state", {})
            scope["state"]["user"] = {"userId": "user-1", "orgId": "org-1"}
        await self.app(scope, receive, send)


def _build_test_app(kb_service=None, kafka_service=None):
    """Build a FastAPI app with user state middleware and mocked deps."""
    app, _kb, _kafka = _build_app(kb_service, kafka_service)

    # Use a simple middleware approach
    from starlette.middleware.base import BaseHTTPMiddleware

    @app.middleware("http")
    async def inject_user(request, call_next):
        request.state.user = {"userId": "user-1", "orgId": "org-1"}
        return await call_next(request)

    return app, _kb, _kafka


# ===========================================================================
# _parse_comma_separated_str tests
# ===========================================================================
class TestParseCommaSeparatedStr:
    def test_none_returns_none(self):
        assert _parse_comma_separated_str(None) is None

    def test_empty_returns_none(self):
        assert _parse_comma_separated_str("") is None

    def test_single_value(self):
        assert _parse_comma_separated_str("abc") == ["abc"]

    def test_multiple_values(self):
        assert _parse_comma_separated_str("a,b,c") == ["a", "b", "c"]

    def test_whitespace_trimmed(self):
        assert _parse_comma_separated_str(" a , b , c ") == ["a", "b", "c"]

    def test_empty_items_filtered(self):
        assert _parse_comma_separated_str("a,,b, ,c") == ["a", "b", "c"]


# ===========================================================================
# Constants tests
# ===========================================================================
class TestConstants:
    def test_http_status_constants(self):
        assert HTTP_MIN_STATUS == 100
        assert HTTP_MAX_STATUS == 600
        assert HTTP_INTERNAL_SERVER_ERROR == 500


# ===========================================================================
# Route handler tests using direct function calls with mocked request
# ===========================================================================

def _make_request(body=None, user=None):
    """Create a mock request with the needed attributes."""
    request = MagicMock()
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    request.state.user = MagicMock()
    request.state.user.get = MagicMock(side_effect=lambda k: user_data.get(k))
    if body is not None:
        request.json = AsyncMock(return_value=body)
    else:
        request.json = AsyncMock(side_effect=Exception("No body"))

    # Mock container
    mock_container = MagicMock()
    mock_container.logger.return_value = MagicMock()
    request.app = MagicMock()
    request.app.container = mock_container
    return request


class TestCreateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": True, "kbId": "kb-1", "message": "Created",
            "id": "kb-1", "name": "Test KB",
            "createdAtTimestamp": 1700000000000,
            "updatedAtTimestamp": 1700000000000,
        }
        request = _make_request(body={"name": "Test KB"})
        result = await create_knowledge_base(request=request, kb_service=kb_svc)
        assert result.id == "kb-1"

    @pytest.mark.asyncio
    async def test_accepts_kbName(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": True, "kbId": "kb-2", "message": "Created",
            "id": "kb-2", "name": "Test KB",
            "createdAtTimestamp": 1700000000000,
            "updatedAtTimestamp": 1700000000000,
        }
        request = _make_request(body={"kbName": "Test KB"})
        result = await create_knowledge_base(request=request, kb_service=kb_svc)
        assert result.id == "kb-2"

    @pytest.mark.asyncio
    async def test_missing_name_raises(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request(body={})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_body_raises(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request()  # No body => json() raises
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": False, "code": 409, "reason": "Duplicate"
        }
        request = _make_request(body={"name": "Dup KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_service_failure_invalid_code(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": False, "code": 9999, "reason": "Bad code"
        }
        request = _make_request(body={"name": "Bad KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.side_effect = RuntimeError("boom")
        request = _make_request(body={"name": "Test KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_name_raises(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request(body={"name": "  "})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_none_result(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = None
        request = _make_request(body={"name": "Test KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500


class TestGetKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.get_knowledge_base.return_value = {
            "success": True, "kb": {"id": "kb-1", "name": "Test"}
        }
        request = _make_request()
        result = await get_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.connectors.sources.localKB.api.kb_router import get_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.get_knowledge_base.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_knowledge_base(kb_id="kb-bad", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        from app.connectors.sources.localKB.api.kb_router import get_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.get_knowledge_base.side_effect = RuntimeError("db error")
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500


class TestUpdateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import update_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.update_knowledge_base.return_value = {"success": True}
        request = _make_request(body={"name": "Updated"})
        result = await update_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result.message == "Knowledge base updated successfully"

    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from app.connectors.sources.localKB.api.kb_router import update_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await update_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import update_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.update_knowledge_base.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request(body={"name": "Updated"})
        with pytest.raises(HTTPException) as exc_info:
            await update_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 403


class TestDeleteKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success_with_events(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_knowledge_base.return_value = {
            "success": True,
            "eventData": {
                "topic": "test-topic",
                "eventType": "DELETE",
                "payloads": [{"id": "r1"}, {"id": "r2"}]
            }
        }
        request = _make_request()
        result = await delete_knowledge_base(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Knowledge base deleted successfully"
        assert kafka_svc.publish_event.await_count == 2

    @pytest.mark.asyncio
    async def test_success_without_events(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_knowledge_base.return_value = {"success": True}
        request = _make_request()
        result = await delete_knowledge_base(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Knowledge base deleted successfully"

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_knowledge_base.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await delete_knowledge_base(
                kb_id="kb-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_kafka_publish_error_does_not_raise(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kafka_svc.publish_event.side_effect = Exception("kafka down")
        kb_svc.delete_knowledge_base.return_value = {
            "success": True,
            "eventData": {
                "topic": "test-topic",
                "eventType": "DELETE",
                "payloads": [{"id": "r1"}]
            }
        }
        request = _make_request()
        result = await delete_knowledge_base(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Knowledge base deleted successfully"


class TestCreateRecordsInKB:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_records_in_kb
        kb_svc = _mock_kb_service()
        kb_svc.create_records_in_kb.return_value = {
            "success": True, "records": [], "message": "ok"
        }
        request = _make_request(body={"records": [{"name": "r1"}]})
        result = await create_records_in_kb(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from app.connectors.sources.localKB.api.kb_router import create_records_in_kb
        kb_svc = _mock_kb_service()
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await create_records_in_kb(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400


class TestUploadRecordsToKB:
    @pytest.mark.asyncio
    async def test_success_with_events(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_kb
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.upload_records_to_kb.return_value = {
            "success": True,
            "eventData": {
                "topic": "t", "eventType": "CREATE",
                "payloads": [{"id": "r1"}]
            }
        }
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/tmp/f", "lastModified": 123}]
        })
        result = await upload_records_to_kb(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_files(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_kb
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        request = _make_request(body={"files": []})
        with pytest.raises(HTTPException) as exc_info:
            await upload_records_to_kb(
                kb_id="kb-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_user_id(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_kb
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/f", "lastModified": 1}]
        }, user={"userId": None, "orgId": None})
        with pytest.raises(HTTPException) as exc_info:
            await upload_records_to_kb(
                kb_id="kb-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 400


class TestUploadRecordsToFolder:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.upload_records_to_folder.return_value = {"success": True}
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/f", "lastModified": 1}]
        })
        result = await upload_records_to_folder(
            kb_id="kb-1", folder_id="f-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.upload_records_to_folder.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/f", "lastModified": 1}]
        })
        with pytest.raises(HTTPException) as exc_info:
            await upload_records_to_folder(
                kb_id="kb-1", folder_id="f-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 404


class TestCreateFolderInKBRoot:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_folder_in_kb_root
        kb_svc = _mock_kb_service()
        kb_svc.create_folder_in_kb.return_value = {
            "success": True, "folderId": "fold-1", "message": "ok"
        }
        request = _make_request(body={"name": "Folder1"})
        result = await create_folder_in_kb_root(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_name(self):
        from app.connectors.sources.localKB.api.kb_router import create_folder_in_kb_root
        kb_svc = _mock_kb_service()
        request = _make_request(body={})
        with pytest.raises(HTTPException) as exc_info:
            await create_folder_in_kb_root(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_accepts_folderName(self):
        from app.connectors.sources.localKB.api.kb_router import create_folder_in_kb_root
        kb_svc = _mock_kb_service()
        kb_svc.create_folder_in_kb.return_value = {
            "success": True, "folderId": "fold-2", "message": "ok"
        }
        request = _make_request(body={"folderName": "Folder2"})
        result = await create_folder_in_kb_root(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True


class TestCreateNestedFolder:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_nested_folder
        kb_svc = _mock_kb_service()
        kb_svc.create_nested_folder.return_value = {
            "success": True, "folderId": "fold-3", "message": "ok"
        }
        request = _make_request(body={"name": "Subfolder"})
        result = await create_nested_folder(
            kb_id="kb-1", parent_folder_id="fold-1",
            request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_name(self):
        from app.connectors.sources.localKB.api.kb_router import create_nested_folder
        kb_svc = _mock_kb_service()
        request = _make_request(body={})
        with pytest.raises(HTTPException) as exc_info:
            await create_nested_folder(
                kb_id="kb-1", parent_folder_id="fold-1",
                request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestGetFolderContents:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_contents
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_contents.return_value = {
            "success": True, "folders": [], "records": []
        }
        request = _make_request()
        result = await get_folder_contents(
            kb_id="kb-1", folder_id="fold-1",
            request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_contents
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_contents.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_folder_contents(
                kb_id="kb-1", folder_id="fold-bad",
                request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 404


class TestUpdateFolder:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import update_folder
        kb_svc = _mock_kb_service()
        kb_svc.updateFolder.return_value = {"success": True}
        request = _make_request(body={"name": "Renamed"})
        result = await update_folder(
            kb_id="kb-1", folder_id="fold-1",
            request=request, kb_service=kb_svc
        )
        assert result.message == "Folder updated successfully"

    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from app.connectors.sources.localKB.api.kb_router import update_folder
        kb_svc = _mock_kb_service()
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await update_folder(
                kb_id="kb-1", folder_id="fold-1",
                request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestDeleteFolder:
    @pytest.mark.asyncio
    async def test_success_with_events(self):
        from app.connectors.sources.localKB.api.kb_router import delete_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_folder.return_value = {
            "success": True,
            "eventData": {
                "topic": "t", "eventType": "DELETE",
                "payloads": [{"id": "r1"}]
            }
        }
        request = _make_request()
        result = await delete_folder(
            kb_id="kb-1", folder_id="fold-1",
            request=request, kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Folder deleted successfully"

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import delete_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_folder.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await delete_folder(
                kb_id="kb-1", folder_id="fold-1",
                request=request, kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 403


class TestListKBRecords:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import list_kb_records
        kb_svc = _mock_kb_service()
        kb_svc.list_kb_records.return_value = {
            "success": True, "records": [], "total": 0
        }
        request = _make_request()
        result = await list_kb_records(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True


class TestGetKBChildren:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_kb_children
        kb_svc = _mock_kb_service()
        kb_svc.get_kb_children.return_value = {
            "success": True, "items": []
        }
        request = _make_request()
        result = await get_kb_children(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import get_kb_children
        kb_svc = _mock_kb_service()
        kb_svc.get_kb_children.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_kb_children(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_none_result(self):
        from app.connectors.sources.localKB.api.kb_router import get_kb_children
        kb_svc = _mock_kb_service()
        kb_svc.get_kb_children.return_value = None
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_kb_children(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 500


class TestGetFolderChildren:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_children
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_children.return_value = {"success": True, "items": []}
        request = _make_request()
        result = await get_folder_children(
            kb_id="kb-1", folder_id="f-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_children
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_children.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_folder_children(
                kb_id="kb-1", folder_id="f-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 404


class TestCreateKBPermissions:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_kb_permissions
        kb_svc = _mock_kb_service()
        kb_svc.create_kb_permissions.return_value = {
            "success": True, "message": "Permissions created"
        }
        request = _make_request(body={
            "role": "editor", "userIds": ["u1"], "teamIds": []
        })
        result = await create_kb_permissions(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_role_for_users(self):
        from app.connectors.sources.localKB.api.kb_router import create_kb_permissions
        kb_svc = _mock_kb_service()
        request = _make_request(body={"userIds": ["u1"]})
        with pytest.raises(HTTPException) as exc_info:
            await create_kb_permissions(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestUpdateKBPermission:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import update_kb_permission
        kb_svc = _mock_kb_service()
        kb_svc.update_kb_permission.return_value = {
            "success": True, "message": "Updated"
        }
        request = _make_request(body={
            "role": "viewer", "userIds": ["u1"]
        })
        result = await update_kb_permission(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_role(self):
        from app.connectors.sources.localKB.api.kb_router import update_kb_permission
        kb_svc = _mock_kb_service()
        request = _make_request(body={"userIds": ["u1"]})
        with pytest.raises(HTTPException) as exc_info:
            await update_kb_permission(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_teams_rejected(self):
        from app.connectors.sources.localKB.api.kb_router import update_kb_permission
        kb_svc = _mock_kb_service()
        request = _make_request(body={
            "role": "editor", "teamIds": ["t1"]
        })
        with pytest.raises(HTTPException) as exc_info:
            await update_kb_permission(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestRemoveKBPermission:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import remove_kb_permission
        kb_svc = _mock_kb_service()
        kb_svc.remove_kb_permission.return_value = {"success": True, "message": "Removed"}
        request = _make_request(body={"userIds": ["u1"]})
        result = await remove_kb_permission(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import remove_kb_permission
        kb_svc = _mock_kb_service()
        kb_svc.remove_kb_permission.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request(body={"userIds": ["u1"]})
        with pytest.raises(HTTPException) as exc_info:
            await remove_kb_permission(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 403


class TestListKBPermissions:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import list_kb_permissions
        kb_svc = _mock_kb_service()
        kb_svc.list_kb_permissions.return_value = {
            "success": True, "permissions": []
        }
        request = _make_request()
        result = await list_kb_permissions(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import list_kb_permissions
        kb_svc = _mock_kb_service()
        kb_svc.list_kb_permissions.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await list_kb_permissions(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 403


class TestListUserKnowledgeBases:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.return_value = {
            "success": True, "knowledgeBases": [], "total": 0
        }
        request = _make_request()
        result = await list_user_knowledge_bases(
            request=request, page=1, limit=20, search=None,
            permissions=None, sort_by="name", sort_order="asc",
            kb_service=kb_svc,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_with_permissions_filter(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.return_value = {
            "success": True, "knowledgeBases": [], "total": 0
        }
        request = _make_request()
        result = await list_user_knowledge_bases(
            request=request, page=1, limit=20, search=None,
            permissions="owner,editor", sort_by="name", sort_order="asc",
            kb_service=kb_svc,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.return_value = {
            "success": False, "code": 500, "reason": "Internal"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await list_user_knowledge_bases(
                request=request, page=1, limit=20, search=None,
                permissions=None, sort_by="name", sort_order="asc",
                kb_service=kb_svc,
            )
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.side_effect = RuntimeError("fail")
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await list_user_knowledge_bases(
                request=request, page=1, limit=20, search=None,
                permissions=None, sort_by="name", sort_order="asc",
                kb_service=kb_svc,
            )
        assert exc_info.value.status_code == 500

# =============================================================================
# Merged from test_kb_router_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mock_kb_service():
    svc = AsyncMock()
    svc.create_knowledge_base = AsyncMock()
    svc.list_user_knowledge_bases = AsyncMock()
    svc.get_knowledge_base = AsyncMock()
    svc.update_knowledge_base = AsyncMock()
    svc.delete_knowledge_base = AsyncMock()
    svc.create_records_in_kb = AsyncMock()
    svc.upload_records_to_kb = AsyncMock()
    svc.upload_records_to_folder = AsyncMock()
    svc.create_folder_in_kb = AsyncMock()
    svc.create_nested_folder = AsyncMock()
    svc.get_folder_contents = AsyncMock()
    svc.updateFolder = AsyncMock()
    svc.delete_folder = AsyncMock()
    svc.list_kb_records = AsyncMock()
    svc.get_kb_children = AsyncMock()
    svc.get_folder_children = AsyncMock()
    svc.create_kb_permissions = AsyncMock()
    svc.update_kb_permission = AsyncMock()
    svc.remove_kb_permission = AsyncMock()
    svc.list_kb_permissions = AsyncMock()
    svc.create_records_in_folder = AsyncMock()
    svc.update_record = AsyncMock()
    svc.delete_record = AsyncMock()
    return svc


def _mock_kafka_service():
    svc = AsyncMock()
    svc.publish_event = AsyncMock()
    return svc


def _build_app(kb_service=None, kafka_service=None):
    """Build a FastAPI app with the kb_router and mocked deps."""
    app = FastAPI()
    _kb = kb_service or _mock_kb_service()
    _kafka = kafka_service or _mock_kafka_service()

    # Mock out the auth dependency
    async def _noop_auth():
        pass

    # Override all dependencies
    app.dependency_overrides[get_kb_service] = lambda: _kb
    app.dependency_overrides[get_kafka_service] = lambda: _kafka

    # Attach container for routes that access request.app.container
    mock_container = MagicMock()
    mock_container.logger.return_value = MagicMock()
    mock_container.kafka_service.return_value = _kafka
    app.container = mock_container

    # Mock graph_provider
    app.state.graph_provider = MagicMock()

    # Include the router
    app.include_router(kb_router)

    return app, _kb, _kafka


def _mock_request_state():
    """Middleware-injected user state."""
    return {"userId": "user-1", "orgId": "org-1"}


# We need a middleware to inject request.state.user
class _InjectUserMiddlewareCoverage:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Inject user state into request
            scope.setdefault("state", {})
            scope["state"]["user"] = {"userId": "user-1", "orgId": "org-1"}
        await self.app(scope, receive, send)


def _build_test_app(kb_service=None, kafka_service=None):
    """Build a FastAPI app with user state middleware and mocked deps."""
    app, _kb, _kafka = _build_app(kb_service, kafka_service)

    # Use a simple middleware approach
    from starlette.middleware.base import BaseHTTPMiddleware

    @app.middleware("http")
    async def inject_user(request, call_next):
        request.state.user = {"userId": "user-1", "orgId": "org-1"}
        return await call_next(request)

    return app, _kb, _kafka


# ===========================================================================
# _parse_comma_separated_str tests
# ===========================================================================
class TestParseCommaSeparatedStrCoverage:
    def test_none_returns_none(self):
        assert _parse_comma_separated_str(None) is None

    def test_empty_returns_none(self):
        assert _parse_comma_separated_str("") is None

    def test_single_value(self):
        assert _parse_comma_separated_str("abc") == ["abc"]

    def test_multiple_values(self):
        assert _parse_comma_separated_str("a,b,c") == ["a", "b", "c"]

    def test_whitespace_trimmed(self):
        assert _parse_comma_separated_str(" a , b , c ") == ["a", "b", "c"]

    def test_empty_items_filtered(self):
        assert _parse_comma_separated_str("a,,b, ,c") == ["a", "b", "c"]


# ===========================================================================
# Constants tests
# ===========================================================================
class TestConstantsCoverage:
    def test_http_status_constants(self):
        assert HTTP_MIN_STATUS == 100
        assert HTTP_MAX_STATUS == 600
        assert HTTP_INTERNAL_SERVER_ERROR == 500


# ===========================================================================
# Route handler tests using direct function calls with mocked request
# ===========================================================================

def _make_request(body=None, user=None):
    """Create a mock request with the needed attributes."""
    request = MagicMock()
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    request.state.user = MagicMock()
    request.state.user.get = MagicMock(side_effect=lambda k: user_data.get(k))
    if body is not None:
        request.json = AsyncMock(return_value=body)
    else:
        request.json = AsyncMock(side_effect=Exception("No body"))

    # Mock container
    mock_container = MagicMock()
    mock_container.logger.return_value = MagicMock()
    request.app = MagicMock()
    request.app.container = mock_container
    return request


class TestCreateKnowledgeBaseCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": True, "kbId": "kb-1", "message": "Created",
            "id": "kb-1", "name": "Test KB",
            "createdAtTimestamp": 1700000000000,
            "updatedAtTimestamp": 1700000000000,
        }
        request = _make_request(body={"name": "Test KB"})
        result = await create_knowledge_base(request=request, kb_service=kb_svc)
        assert result.id == "kb-1"

    @pytest.mark.asyncio
    async def test_accepts_kbName(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": True, "kbId": "kb-2", "message": "Created",
            "id": "kb-2", "name": "Test KB",
            "createdAtTimestamp": 1700000000000,
            "updatedAtTimestamp": 1700000000000,
        }
        request = _make_request(body={"kbName": "Test KB"})
        result = await create_knowledge_base(request=request, kb_service=kb_svc)
        assert result.id == "kb-2"

    @pytest.mark.asyncio
    async def test_missing_name_raises(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request(body={})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_body_raises(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request()  # No body => json() raises
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": False, "code": 409, "reason": "Duplicate"
        }
        request = _make_request(body={"name": "Dup KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_service_failure_invalid_code(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = {
            "success": False, "code": 9999, "reason": "Bad code"
        }
        request = _make_request(body={"name": "Bad KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.side_effect = RuntimeError("boom")
        request = _make_request(body={"name": "Test KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_name_raises(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request(body={"name": "  "})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_none_result(self):
        from app.connectors.sources.localKB.api.kb_router import create_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.create_knowledge_base.return_value = None
        request = _make_request(body={"name": "Test KB"})
        with pytest.raises(HTTPException) as exc_info:
            await create_knowledge_base(request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500


class TestGetKnowledgeBaseCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.get_knowledge_base.return_value = {
            "success": True, "kb": {"id": "kb-1", "name": "Test"}
        }
        request = _make_request()
        result = await get_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.connectors.sources.localKB.api.kb_router import get_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.get_knowledge_base.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_knowledge_base(kb_id="kb-bad", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        from app.connectors.sources.localKB.api.kb_router import get_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.get_knowledge_base.side_effect = RuntimeError("db error")
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 500


class TestUpdateKnowledgeBaseCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import update_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.update_knowledge_base.return_value = {"success": True}
        request = _make_request(body={"name": "Updated"})
        result = await update_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result.message == "Knowledge base updated successfully"

    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from app.connectors.sources.localKB.api.kb_router import update_knowledge_base
        kb_svc = _mock_kb_service()
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await update_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import update_knowledge_base
        kb_svc = _mock_kb_service()
        kb_svc.update_knowledge_base.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request(body={"name": "Updated"})
        with pytest.raises(HTTPException) as exc_info:
            await update_knowledge_base(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 403


class TestDeleteKnowledgeBaseCoverage:
    @pytest.mark.asyncio
    async def test_success_with_events(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_knowledge_base.return_value = {
            "success": True,
            "eventData": {
                "topic": "test-topic",
                "eventType": "DELETE",
                "payloads": [{"id": "r1"}, {"id": "r2"}]
            }
        }
        request = _make_request()
        result = await delete_knowledge_base(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Knowledge base deleted successfully"
        assert kafka_svc.publish_event.await_count == 2

    @pytest.mark.asyncio
    async def test_success_without_events(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_knowledge_base.return_value = {"success": True}
        request = _make_request()
        result = await delete_knowledge_base(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Knowledge base deleted successfully"

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_knowledge_base.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await delete_knowledge_base(
                kb_id="kb-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_kafka_publish_error_does_not_raise(self):
        from app.connectors.sources.localKB.api.kb_router import delete_knowledge_base
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kafka_svc.publish_event.side_effect = Exception("kafka down")
        kb_svc.delete_knowledge_base.return_value = {
            "success": True,
            "eventData": {
                "topic": "test-topic",
                "eventType": "DELETE",
                "payloads": [{"id": "r1"}]
            }
        }
        request = _make_request()
        result = await delete_knowledge_base(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Knowledge base deleted successfully"


class TestCreateRecordsInKBCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_records_in_kb
        kb_svc = _mock_kb_service()
        kb_svc.create_records_in_kb.return_value = {
            "success": True, "records": [], "message": "ok"
        }
        request = _make_request(body={"records": [{"name": "r1"}]})
        result = await create_records_in_kb(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from app.connectors.sources.localKB.api.kb_router import create_records_in_kb
        kb_svc = _mock_kb_service()
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await create_records_in_kb(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400


class TestUploadRecordsToKBCoverage:
    @pytest.mark.asyncio
    async def test_success_with_events(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_kb
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.upload_records_to_kb.return_value = {
            "success": True,
            "eventData": {
                "topic": "t", "eventType": "CREATE",
                "payloads": [{"id": "r1"}]
            }
        }
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/tmp/f", "lastModified": 123}]
        })
        result = await upload_records_to_kb(
            kb_id="kb-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_files(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_kb
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        request = _make_request(body={"files": []})
        with pytest.raises(HTTPException) as exc_info:
            await upload_records_to_kb(
                kb_id="kb-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_user_id(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_kb
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/f", "lastModified": 1}]
        }, user={"userId": None, "orgId": None})
        with pytest.raises(HTTPException) as exc_info:
            await upload_records_to_kb(
                kb_id="kb-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 400


class TestUploadRecordsToFolderCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.upload_records_to_folder.return_value = {"success": True}
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/f", "lastModified": 1}]
        })
        result = await upload_records_to_folder(
            kb_id="kb-1", folder_id="f-1", request=request,
            kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import upload_records_to_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.upload_records_to_folder.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request(body={
            "files": [{"record": {}, "fileRecord": {}, "filePath": "/f", "lastModified": 1}]
        })
        with pytest.raises(HTTPException) as exc_info:
            await upload_records_to_folder(
                kb_id="kb-1", folder_id="f-1", request=request,
                kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 404


class TestCreateFolderInKBRootCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_folder_in_kb_root
        kb_svc = _mock_kb_service()
        kb_svc.create_folder_in_kb.return_value = {
            "success": True, "folderId": "fold-1", "message": "ok"
        }
        request = _make_request(body={"name": "Folder1"})
        result = await create_folder_in_kb_root(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_name(self):
        from app.connectors.sources.localKB.api.kb_router import create_folder_in_kb_root
        kb_svc = _mock_kb_service()
        request = _make_request(body={})
        with pytest.raises(HTTPException) as exc_info:
            await create_folder_in_kb_root(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_accepts_folderName(self):
        from app.connectors.sources.localKB.api.kb_router import create_folder_in_kb_root
        kb_svc = _mock_kb_service()
        kb_svc.create_folder_in_kb.return_value = {
            "success": True, "folderId": "fold-2", "message": "ok"
        }
        request = _make_request(body={"folderName": "Folder2"})
        result = await create_folder_in_kb_root(kb_id="kb-1", request=request, kb_service=kb_svc)
        assert result["success"] is True


class TestCreateNestedFolderCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_nested_folder
        kb_svc = _mock_kb_service()
        kb_svc.create_nested_folder.return_value = {
            "success": True, "folderId": "fold-3", "message": "ok"
        }
        request = _make_request(body={"name": "Subfolder"})
        result = await create_nested_folder(
            kb_id="kb-1", parent_folder_id="fold-1",
            request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_name(self):
        from app.connectors.sources.localKB.api.kb_router import create_nested_folder
        kb_svc = _mock_kb_service()
        request = _make_request(body={})
        with pytest.raises(HTTPException) as exc_info:
            await create_nested_folder(
                kb_id="kb-1", parent_folder_id="fold-1",
                request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestGetFolderContentsCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_contents
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_contents.return_value = {
            "success": True, "folders": [], "records": []
        }
        request = _make_request()
        result = await get_folder_contents(
            kb_id="kb-1", folder_id="fold-1",
            request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_contents
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_contents.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_folder_contents(
                kb_id="kb-1", folder_id="fold-bad",
                request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 404


class TestUpdateFolderCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import update_folder
        kb_svc = _mock_kb_service()
        kb_svc.updateFolder.return_value = {"success": True}
        request = _make_request(body={"name": "Renamed"})
        result = await update_folder(
            kb_id="kb-1", folder_id="fold-1",
            request=request, kb_service=kb_svc
        )
        assert result.message == "Folder updated successfully"

    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from app.connectors.sources.localKB.api.kb_router import update_folder
        kb_svc = _mock_kb_service()
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await update_folder(
                kb_id="kb-1", folder_id="fold-1",
                request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestDeleteFolderCoverage:
    @pytest.mark.asyncio
    async def test_success_with_events(self):
        from app.connectors.sources.localKB.api.kb_router import delete_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_folder.return_value = {
            "success": True,
            "eventData": {
                "topic": "t", "eventType": "DELETE",
                "payloads": [{"id": "r1"}]
            }
        }
        request = _make_request()
        result = await delete_folder(
            kb_id="kb-1", folder_id="fold-1",
            request=request, kb_service=kb_svc, kafka_service=kafka_svc
        )
        assert result.message == "Folder deleted successfully"

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import delete_folder
        kb_svc = _mock_kb_service()
        kafka_svc = _mock_kafka_service()
        kb_svc.delete_folder.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await delete_folder(
                kb_id="kb-1", folder_id="fold-1",
                request=request, kb_service=kb_svc, kafka_service=kafka_svc
            )
        assert exc_info.value.status_code == 403


class TestListKBRecordsCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import list_kb_records
        kb_svc = _mock_kb_service()
        kb_svc.list_kb_records.return_value = {
            "success": True, "records": [], "total": 0
        }
        request = _make_request()
        result = await list_kb_records(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True


class TestGetKBChildrenCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_kb_children
        kb_svc = _mock_kb_service()
        kb_svc.get_kb_children.return_value = {
            "success": True, "items": []
        }
        request = _make_request()
        result = await get_kb_children(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import get_kb_children
        kb_svc = _mock_kb_service()
        kb_svc.get_kb_children.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_kb_children(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_none_result(self):
        from app.connectors.sources.localKB.api.kb_router import get_kb_children
        kb_svc = _mock_kb_service()
        kb_svc.get_kb_children.return_value = None
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_kb_children(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 500


class TestGetFolderChildrenCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_children
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_children.return_value = {"success": True, "items": []}
        request = _make_request()
        result = await get_folder_children(
            kb_id="kb-1", folder_id="f-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import get_folder_children
        kb_svc = _mock_kb_service()
        kb_svc.get_folder_children.return_value = {
            "success": False, "code": 404, "reason": "Not found"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_folder_children(
                kb_id="kb-1", folder_id="f-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 404


class TestCreateKBPermissionsCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import create_kb_permissions
        kb_svc = _mock_kb_service()
        kb_svc.create_kb_permissions.return_value = {
            "success": True, "message": "Permissions created"
        }
        request = _make_request(body={
            "role": "editor", "userIds": ["u1"], "teamIds": []
        })
        result = await create_kb_permissions(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_role_for_users(self):
        from app.connectors.sources.localKB.api.kb_router import create_kb_permissions
        kb_svc = _mock_kb_service()
        request = _make_request(body={"userIds": ["u1"]})
        with pytest.raises(HTTPException) as exc_info:
            await create_kb_permissions(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestUpdateKBPermissionCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import update_kb_permission
        kb_svc = _mock_kb_service()
        kb_svc.update_kb_permission.return_value = {
            "success": True, "message": "Updated"
        }
        request = _make_request(body={
            "role": "viewer", "userIds": ["u1"]
        })
        result = await update_kb_permission(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_role(self):
        from app.connectors.sources.localKB.api.kb_router import update_kb_permission
        kb_svc = _mock_kb_service()
        request = _make_request(body={"userIds": ["u1"]})
        with pytest.raises(HTTPException) as exc_info:
            await update_kb_permission(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_teams_rejected(self):
        from app.connectors.sources.localKB.api.kb_router import update_kb_permission
        kb_svc = _mock_kb_service()
        request = _make_request(body={
            "role": "editor", "teamIds": ["t1"]
        })
        with pytest.raises(HTTPException) as exc_info:
            await update_kb_permission(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 400


class TestRemoveKBPermissionCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import remove_kb_permission
        kb_svc = _mock_kb_service()
        kb_svc.remove_kb_permission.return_value = {"success": True, "message": "Removed"}
        request = _make_request(body={"userIds": ["u1"]})
        result = await remove_kb_permission(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import remove_kb_permission
        kb_svc = _mock_kb_service()
        kb_svc.remove_kb_permission.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request(body={"userIds": ["u1"]})
        with pytest.raises(HTTPException) as exc_info:
            await remove_kb_permission(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 403


class TestListKBPermissionsCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import list_kb_permissions
        kb_svc = _mock_kb_service()
        kb_svc.list_kb_permissions.return_value = {
            "success": True, "permissions": []
        }
        request = _make_request()
        result = await list_kb_permissions(
            kb_id="kb-1", request=request, kb_service=kb_svc
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.connectors.sources.localKB.api.kb_router import list_kb_permissions
        kb_svc = _mock_kb_service()
        kb_svc.list_kb_permissions.return_value = {
            "success": False, "code": 403, "reason": "Forbidden"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await list_kb_permissions(
                kb_id="kb-1", request=request, kb_service=kb_svc
            )
        assert exc_info.value.status_code == 403


class TestListUserKnowledgeBasesCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.return_value = {
            "success": True, "knowledgeBases": [], "total": 0
        }
        request = _make_request()
        result = await list_user_knowledge_bases(
            request=request, page=1, limit=20, search=None,
            permissions=None, sort_by="name", sort_order="asc",
            kb_service=kb_svc,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_with_permissions_filter(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.return_value = {
            "success": True, "knowledgeBases": [], "total": 0
        }
        request = _make_request()
        result = await list_user_knowledge_bases(
            request=request, page=1, limit=20, search=None,
            permissions="owner,editor", sort_by="name", sort_order="asc",
            kb_service=kb_svc,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_service_failure(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.return_value = {
            "success": False, "code": 500, "reason": "Internal"
        }
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await list_user_knowledge_bases(
                request=request, page=1, limit=20, search=None,
                permissions=None, sort_by="name", sort_order="asc",
                kb_service=kb_svc,
            )
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        from app.connectors.sources.localKB.api.kb_router import list_user_knowledge_bases
        kb_svc = _mock_kb_service()
        kb_svc.list_user_knowledge_bases.side_effect = RuntimeError("fail")
        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await list_user_knowledge_bases(
                request=request, page=1, limit=20, search=None,
                permissions=None, sort_by="name", sort_order="asc",
                kb_service=kb_svc,
            )
        assert exc_info.value.status_code == 500
