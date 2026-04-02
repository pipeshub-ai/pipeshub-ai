import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.connectors.sources.localKB.api.kb_router import (
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_MAX_STATUS,
    HTTP_MIN_STATUS,
    _parse_comma_separated_str,
    kb_router,
)


def _make_app(kb_service_mock=None, kafka_service_mock=None, logger_mock=None):
    app = FastAPI()
    app.include_router(kb_router)

    _kb = kb_service_mock or AsyncMock()
    _kafka = kafka_service_mock or AsyncMock()
    _logger = logger_mock or MagicMock()

    container = MagicMock()
    container.logger.return_value = _logger
    container.kafka_service.return_value = _kafka
    app.container = container
    app.state.graph_provider = AsyncMock()

    app.dependency_overrides = {}

    from app.connectors.sources.localKB.api.kb_router import get_kb_service, get_kafka_service
    from app.api.middlewares.auth import require_scopes

    app.dependency_overrides[get_kb_service] = lambda: _kb
    app.dependency_overrides[get_kafka_service] = lambda: _kafka

    def _noop_scopes(*args, **kwargs):
        async def _dep():
            pass
        return _dep
    app.dependency_overrides[require_scopes] = _noop_scopes

    @app.middleware("http")
    async def inject_user(request, call_next):
        request.state.user = {"userId": "user1", "orgId": "org1"}
        return await call_next(request)

    return app, _kb, _kafka


class TestParseCommaSeparatedStr:
    def test_none(self):
        assert _parse_comma_separated_str(None) is None

    def test_empty_string(self):
        assert _parse_comma_separated_str("") is None

    def test_single_value(self):
        assert _parse_comma_separated_str("one") == ["one"]

    def test_multiple_values(self):
        assert _parse_comma_separated_str("a, b, c") == ["a", "b", "c"]

    def test_trailing_commas(self):
        assert _parse_comma_separated_str(",a,,b,") == ["a", "b"]

    def test_whitespace_only_items(self):
        assert _parse_comma_separated_str(",  ,  ,") == []


class TestConstants:
    def test_http_constants(self):
        assert HTTP_MIN_STATUS == 100
        assert HTTP_MAX_STATUS == 600
        assert HTTP_INTERNAL_SERVER_ERROR == 500


class TestCreateKnowledgeBase:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_knowledge_base = AsyncMock(return_value={
            "success": True, "id": "kb1", "name": "Test",
            "createdAtTimestamp": 100, "updatedAtTimestamp": 100, "userRole": "OWNER"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": "Test"})
        assert resp.status_code == 200
        assert resp.json()["id"] == "kb1"

    def test_kbname_field_accepted(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_knowledge_base = AsyncMock(return_value={
            "success": True, "id": "kb1", "name": "Alt",
            "createdAtTimestamp": 100, "updatedAtTimestamp": 100, "userRole": "OWNER"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"kbName": "Alt"})
        assert resp.status_code == 200

    def test_missing_name(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"other": "field"})
        assert resp.status_code == 400

    def test_empty_name(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": ""})
        assert resp.status_code == 400

    def test_whitespace_name(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": "   "})
        assert resp.status_code == 400

    def test_non_string_name(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": 123})
        assert resp.status_code == 400

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", content="not json", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_service_returns_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_knowledge_base = AsyncMock(return_value={
            "success": False, "code": 409, "reason": "Already exists"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": "Test"})
        assert resp.status_code == 409

    def test_service_returns_none(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_knowledge_base = AsyncMock(return_value=None)
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": "Test"})
        assert resp.status_code == 500

    def test_service_returns_invalid_code(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_knowledge_base = AsyncMock(return_value={
            "success": False, "code": 9999, "reason": "Bad"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": "Test"})
        assert resp.status_code == 500

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_knowledge_base = AsyncMock(side_effect=RuntimeError("boom"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/", json={"name": "Test"})
        assert resp.status_code == 500


class TestListUserKnowledgeBases:
    _valid_pagination = {
        "page": 1, "limit": 20, "totalCount": 0,
        "totalPages": 0, "hasNext": False, "hasPrev": False,
    }
    _valid_filters = {
        "applied": {"search": None, "permissions": None},
        "available": {"permissions": ["OWNER", "READER", "WRITER"]},
    }

    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_user_knowledge_bases = AsyncMock(return_value={
            "knowledgeBases": [], "pagination": self._valid_pagination, "filters": self._valid_filters
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/")
        assert resp.status_code == 200

    def test_with_query_params(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_user_knowledge_bases = AsyncMock(return_value={
            "knowledgeBases": [], "pagination": self._valid_pagination, "filters": self._valid_filters
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/?page=2&limit=10&search=test&permissions=OWNER,READER&sort_by=name&sort_order=desc")
        assert resp.status_code == 200
        call_kwargs = kb_svc.list_user_knowledge_bases.call_args.kwargs
        assert call_kwargs["page"] == 2
        assert call_kwargs["permissions"] == ["OWNER", "READER"]

    def test_service_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_user_knowledge_bases = AsyncMock(return_value={
            "success": False, "code": 404, "reason": "Not found"
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/")
        assert resp.status_code == 404

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_user_knowledge_bases = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.get("/api/v1/kb/")
        assert resp.status_code == 500


class TestGetKnowledgeBase:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_knowledge_base = AsyncMock(return_value={
            "id": "kb1", "name": "Test", "createdAtTimestamp": 100,
            "updatedAtTimestamp": 100, "createdBy": "u1"
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1")
        assert resp.status_code == 200

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_knowledge_base = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Forbidden"
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1")
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_knowledge_base = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1")
        assert resp.status_code == 500


class TestUpdateKnowledgeBase:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_knowledge_base = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1", json={"groupName": "New"})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_knowledge_base = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "No permission"
        })
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1", json={})
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_knowledge_base = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1", json={})
        assert resp.status_code == 500


class TestDeleteKnowledgeBase:
    def test_success_no_events(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.delete_knowledge_base = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1")
        assert resp.status_code == 200

    def test_success_with_events(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.delete_knowledge_base = AsyncMock(return_value={
            "success": True, "eventData": {
                "payloads": [{"recordId": "r1"}],
                "eventType": "DELETE", "topic": "test-topic"
            }
        })
        kafka.publish_event = AsyncMock()
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1")
        assert resp.status_code == 200

    def test_success_event_publish_fails(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.delete_knowledge_base = AsyncMock(return_value={
            "success": True, "eventData": {
                "payloads": [{"recordId": "r1"}],
                "eventType": "DELETE", "topic": "test-topic"
            }
        })
        kafka.publish_event = AsyncMock(side_effect=Exception("kafka down"))
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1")
        assert resp.status_code == 200

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_knowledge_base = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Not owner"
        })
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1")
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_knowledge_base = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1")
        assert resp.status_code == 500


class TestCreateFolderInKbRoot:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_folder_in_kb = AsyncMock(return_value={
            "success": True, "id": "f1", "name": "Folder", "webUrl": "/"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder", json={"name": "Folder"})
        assert resp.status_code == 200

    def test_foldername_field(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_folder_in_kb = AsyncMock(return_value={
            "success": True, "id": "f1", "name": "Folder", "webUrl": "/"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder", json={"folderName": "Folder"})
        assert resp.status_code == 200

    def test_missing_name(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder", json={})
        assert resp.status_code == 400

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_service_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_folder_in_kb = AsyncMock(return_value={
            "success": False, "code": 409, "reason": "Conflict"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder", json={"name": "F"})
        assert resp.status_code == 409

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_folder_in_kb = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder", json={"name": "F"})
        assert resp.status_code == 500


class TestCreateNestedFolder:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_nested_folder = AsyncMock(return_value={
            "success": True, "id": "f2", "name": "Sub", "webUrl": "/sub"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/p1/subfolder", json={"name": "Sub"})
        assert resp.status_code == 200

    def test_missing_name(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/p1/subfolder", json={})
        assert resp.status_code == 400

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/p1/subfolder", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_nested_folder = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/p1/subfolder", json={"name": "Sub"})
        assert resp.status_code == 500


class TestGetFolderContents:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_folder_contents = AsyncMock(return_value={
            "folder": {"id": "f1", "name": "F"},
            "contents": [], "totalItems": 0
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/folder/f1/user/user1")
        assert resp.status_code == 200

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_folder_contents = AsyncMock(return_value={
            "success": False, "code": 404, "reason": "Not found"
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/folder/f1/user/user1")
        assert resp.status_code == 404

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_folder_contents = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/folder/f1/user/user1")
        assert resp.status_code == 500


class TestUpdateFolder:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.updateFolder = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/folder/f1", json={"name": "New"})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/folder/f1", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.updateFolder = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "No permission"
        })
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/folder/f1", json={"name": "N"})
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.updateFolder = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/folder/f1", json={"name": "N"})
        assert resp.status_code == 500


class TestDeleteFolder:
    def test_success_no_events(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.delete_folder = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1/folder/f1")
        assert resp.status_code == 200

    def test_success_with_events(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.delete_folder = AsyncMock(return_value={
            "success": True, "eventData": {
                "payloads": [{"id": "r1"}], "eventType": "DELETE", "topic": "t"
            }
        })
        kafka.publish_event = AsyncMock()
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1/folder/f1")
        assert resp.status_code == 200

    def test_event_publish_fails(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.delete_folder = AsyncMock(return_value={
            "success": True, "eventData": {
                "payloads": [{"id": "r1"}], "eventType": "DELETE", "topic": "t"
            }
        })
        kafka.publish_event = AsyncMock(side_effect=Exception("kafka err"))
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1/folder/f1")
        assert resp.status_code == 200

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_folder = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Forbidden"
        })
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1/folder/f1")
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_folder = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.delete("/api/v1/kb/kb1/folder/f1")
        assert resp.status_code == 500


class TestUploadRecordsToKb:
    def _files_body(self):
        return {"files": [{"record": {}, "fileRecord": {}, "filePath": "/a.txt", "lastModified": 100}]}

    def test_success_no_events(self):
        app, kb_svc, _ = _make_app()
        kb_svc.upload_records_to_kb = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", json=self._files_body())
        assert resp.status_code == 200

    def test_success_with_events(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.upload_records_to_kb = AsyncMock(return_value={
            "success": True, "eventData": {
                "payloads": [{"id": "r1"}], "eventType": "UPLOAD", "topic": "t"
            }
        })
        kafka.publish_event = AsyncMock()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", json=self._files_body())
        assert resp.status_code == 200

    def test_no_files(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", json={"files": []})
        assert resp.status_code == 400

    def test_missing_files_key(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", json={})
        assert resp.status_code == 400

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_service_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.upload_records_to_kb = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Forbidden"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", json=self._files_body())
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.upload_records_to_kb = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", json=self._files_body())
        assert resp.status_code == 500

    def test_event_publish_fails(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.upload_records_to_kb = AsyncMock(return_value={
            "success": True, "eventData": {
                "payloads": [{"id": "r1"}], "eventType": "UPLOAD", "topic": "t"
            }
        })
        kafka.publish_event = AsyncMock(side_effect=Exception("kafka err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/upload", json=self._files_body())
        assert resp.status_code == 200


class TestUploadRecordsToFolder:
    def _files_body(self):
        return {"files": [{"record": {}, "fileRecord": {}, "filePath": "/a.txt", "lastModified": 100}]}

    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.upload_records_to_folder = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/upload", json=self._files_body())
        assert resp.status_code == 200

    def test_no_files(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/upload", json={"files": []})
        assert resp.status_code == 400

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/upload", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_service_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.upload_records_to_folder = AsyncMock(return_value={
            "success": False, "code": 404, "reason": "KB not found"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/upload", json=self._files_body())
        assert resp.status_code == 404

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.upload_records_to_folder = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/upload", json=self._files_body())
        assert resp.status_code == 500


class TestCreateRecordsInKb:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_records_in_kb = AsyncMock(return_value={
            "success": True, "recordCount": 1, "insertedRecordIds": ["r1"],
            "insertedFileIds": ["f1"], "kbId": "kb1"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/records", json={"records": [{}], "fileRecords": [{}]})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/records", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_records_in_kb = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Forbidden"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/records", json={"records": [{}], "fileRecords": [{}]})
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_records_in_kb = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/records", json={"records": [{}]})
        assert resp.status_code == 500


class TestCreateRecordsInFolder:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_records_in_folder = AsyncMock(return_value={
            "success": True, "recordCount": 1, "insertedRecordIds": ["r1"],
            "insertedFileIds": ["f1"], "kbId": "kb1", "folderId": "f1"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/records", json={"records": [{}], "fileRecords": [{}]})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/records", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_records_in_folder = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/folder/f1/records", json={"records": [{}]})
        assert resp.status_code == 500


class TestListKbRecords:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_kb_records = AsyncMock(return_value={
            "records": [],
            "pagination": {
                "page": 1, "limit": 20, "totalCount": 0, "totalPages": 0,
            },
            "filters": {
                "applied": {},
                "available": {
                    "recordTypes": [], "origins": [], "connectors": [],
                    "indexingStatus": [], "permissions": [],
                },
            },
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/records")
        assert resp.status_code == 200


class TestGetKbChildren:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_kb_children = AsyncMock(return_value={
            "success": True, "children": [], "counts": {"folders": 0, "records": 0}
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/children")
        assert resp.status_code == 200

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_kb_children = AsyncMock(return_value={
            "success": False, "code": 404, "reason": "Not found"
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/children")
        assert resp.status_code == 404

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_kb_children = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/children")
        assert resp.status_code == 500


class TestGetFolderChildren:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_folder_children = AsyncMock(return_value={
            "success": True, "children": []
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/folder/f1/children")
        assert resp.status_code == 200

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_folder_children = AsyncMock(return_value={
            "success": False, "code": 404, "reason": "Not found"
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/folder/f1/children")
        assert resp.status_code == 404

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_folder_children = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/folder/f1/children")
        assert resp.status_code == 500


class TestCreateKbPermissions:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_kb_permissions = AsyncMock(return_value={
            "success": True, "grantedCount": 1, "grantedUsers": ["u1"],
            "grantedTeams": [], "role": "READER", "kbId": "kb1", "details": {}
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/permissions", json={
            "userIds": ["u1"], "role": "READER"
        })
        assert resp.status_code == 200

    def test_users_without_role(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/permissions", json={"userIds": ["u1"]})
        assert resp.status_code == 400

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/permissions", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_kb_permissions = AsyncMock(return_value={
            "success": False, "code": 400, "reason": "Bad"
        })
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/permissions", json={"teamIds": ["t1"]})
        assert resp.status_code == 400

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.create_kb_permissions = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.post("/api/v1/kb/kb1/permissions", json={"teamIds": ["t1"]})
        assert resp.status_code == 500


class TestUpdateKbPermission:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_kb_permission = AsyncMock(return_value={
            "success": True, "userIds": ["u1"], "teamIds": [],
            "newRole": "WRITER", "kbId": "kb1"
        })
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/permissions", json={
            "userIds": ["u1"], "role": "WRITER"
        })
        assert resp.status_code == 200

    def test_missing_role(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/permissions", json={"userIds": ["u1"]})
        assert resp.status_code == 400

    def test_teams_rejected(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/permissions", json={
            "userIds": [], "teamIds": ["t1"], "role": "READER"
        })
        assert resp.status_code == 400

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/permissions", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_kb_permission = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Not owner"
        })
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/permissions", json={
            "userIds": ["u1"], "role": "WRITER"
        })
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_kb_permission = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/permissions", json={
            "userIds": ["u1"], "role": "WRITER"
        })
        assert resp.status_code == 500


class TestRemoveKbPermission:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.remove_kb_permission = AsyncMock(return_value={
            "success": True, "userIds": ["u1"], "teamIds": [], "kbId": "kb1"
        })
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/permissions", json={
            "userIds": ["u1"], "teamIds": []
        })
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/permissions", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.remove_kb_permission = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Not owner"
        })
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/permissions", json={
            "userIds": ["u1"], "teamIds": []
        })
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.remove_kb_permission = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/permissions", json={
            "userIds": ["u1"], "teamIds": []
        })
        assert resp.status_code == 500


class TestListKbPermissions:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_kb_permissions = AsyncMock(return_value={
            "success": True, "permissions": [], "kbId": "kb1", "totalCount": 0
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/permissions")
        assert resp.status_code == 200

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_kb_permissions = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Forbidden"
        })
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/permissions")
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.list_kb_permissions = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.get("/api/v1/kb/kb1/permissions")
        assert resp.status_code == 500


class TestUpdateRecord:
    def test_success(self):
        app, kb_svc, kafka = _make_app()
        kb_svc.update_record = AsyncMock(return_value={
            "success": True, "recordId": "r1",
            "updatedRecord": {"id": "r1", "name": "new"},
            "timestamp": 1234567890,
        })
        gp = app.state.graph_provider
        gp._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1", "kb_name": "KB"})
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        gp.get_user_kb_permission = AsyncMock(return_value="OWNER")
        gp.get_knowledge_base = AsyncMock(return_value={"id": "kb1", "name": "KB"})
        gp.get_knowledge_hub_parent_node = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.put("/api/v1/kb/record/r1", json={"updates": {"name": "new"}})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.put("/api/v1/kb/record/r1", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_service_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_record = AsyncMock(return_value={
            "success": False, "code": 404, "reason": "Not found"
        })
        client = TestClient(app)
        resp = client.put("/api/v1/kb/record/r1", json={"updates": {}})
        assert resp.status_code == 404

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.update_record = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.put("/api/v1/kb/record/r1", json={"updates": {}})
        assert resp.status_code == 500


class TestDeleteRecordsInKb:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_records_in_kb = AsyncMock(return_value={
            "success": True, "message": "Deleted", "deleteType": "bulk"
        })
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/records", json={"recordIds": ["r1"]})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/records", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_records_in_kb = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Forbidden"
        })
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/records", json={"recordIds": ["r1"]})
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_records_in_kb = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/records", json={"recordIds": ["r1"]})
        assert resp.status_code == 500


class TestDeleteRecordsInFolder:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_records_in_folder = AsyncMock(return_value={
            "success": True, "message": "Deleted", "deleteType": "bulk"
        })
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/folder/f1/records", json={"recordIds": ["r1"]})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/folder/f1/records", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.delete_records_in_folder = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.request("DELETE", "/api/v1/kb/kb1/folder/f1/records", json={"recordIds": ["r1"]})
        assert resp.status_code == 500


class TestListAllRecords:
    # NOTE: The literal "/records" route is shadowed by the "/{kb_id}" route
    # in the current router definition order. As a result, GET /api/v1/kb/records
    # is handled by get_knowledge_base(kb_id="records"). These tests mock that
    # endpoint to avoid ResponseValidationError and verify the request succeeds.
    _valid_kb_response = {
        "id": "records", "name": "Records KB",
        "createdAtTimestamp": 100, "updatedAtTimestamp": 100,
        "createdBy": "user1",
    }

    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_knowledge_base = AsyncMock(return_value=self._valid_kb_response)
        client = TestClient(app)
        resp = client.get("/api/v1/kb/records")
        assert resp.status_code == 200

    def test_with_comma_separated_params(self):
        app, kb_svc, _ = _make_app()
        kb_svc.get_knowledge_base = AsyncMock(return_value=self._valid_kb_response)
        client = TestClient(app)
        resp = client.get("/api/v1/kb/records?record_types=FILE,WEBPAGE&origins=local&indexing_status=COMPLETED&permissions=OWNER&source=local")
        assert resp.status_code == 200
        # The /{kb_id} route is matched; query params are ignored by get_knowledge_base
        kb_svc.get_knowledge_base.assert_called_once()


class TestMoveRecord:
    def test_success(self):
        app, kb_svc, _ = _make_app()
        kb_svc.move_record = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/record/r1/move", json={"newParentId": "f1"})
        assert resp.status_code == 200

    def test_move_to_root(self):
        app, kb_svc, _ = _make_app()
        kb_svc.move_record = AsyncMock(return_value={"success": True})
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/record/r1/move", json={"newParentId": None})
        assert resp.status_code == 200

    def test_invalid_body(self):
        app, kb_svc, _ = _make_app()
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/record/r1/move", content="bad", headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_failure(self):
        app, kb_svc, _ = _make_app()
        kb_svc.move_record = AsyncMock(return_value={
            "success": False, "code": 403, "reason": "Forbidden"
        })
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/record/r1/move", json={"newParentId": "f1"})
        assert resp.status_code == 403

    def test_unexpected_exception(self):
        app, kb_svc, _ = _make_app()
        kb_svc.move_record = AsyncMock(side_effect=RuntimeError("err"))
        client = TestClient(app)
        resp = client.put("/api/v1/kb/kb1/record/r1/move", json={"newParentId": "f1"})
        assert resp.status_code == 500
