"""The restore routes: the caller's ids come from the token, refusals keep their words."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tests.unit.connectors.sources.localKB.test_kb_router import _make_app


class TestRestoreRecordRoute:
    def test_restores_as_the_signed_in_user(self) -> None:
        app, kb, _ = _make_app()
        kb.restore_record = AsyncMock(return_value={
            "success": True, "code": 200, "message": "Restored 1 item(s).", "batchId": "b-1",
            "restoredRecords": [{"recordId": "r1", "name": "a (restored).pdf", "renamedFrom": "a.pdf"}],
        })
        response = TestClient(app).post("/api/v1/kb/record/r1/restore")
        assert response.status_code == 200
        assert response.json() == {
            "success": True, "message": "Restored 1 item(s).", "batchId": "b-1",
            "restoredRecords": [{"recordId": "r1", "name": "a (restored).pdf", "renamedFrom": "a.pdf"}],
        }
        kb.restore_record.assert_awaited_once_with(record_id="r1", user_id="user1", org_id="org1")

    def test_a_refusal_keeps_its_status_and_words(self) -> None:
        app, kb, _ = _make_app()
        kb.restore_record = AsyncMock(return_value={
            "success": False, "code": 409, "reason": "Restore 'Docs' first, then restore 'a.pdf'.",
        })
        response = TestClient(app).post("/api/v1/kb/record/r1/restore")
        assert response.status_code == 409
        assert response.json()["detail"] == "Restore 'Docs' first, then restore 'a.pdf'."

    def test_an_unexpected_error_does_not_leak(self) -> None:
        app, kb, _ = _make_app()
        kb.restore_record = AsyncMock(side_effect=RuntimeError("graph down at 10.0.0.1"))
        response = TestClient(app).post("/api/v1/kb/record/r1/restore")
        assert response.status_code == 500
        assert "10.0.0.1" not in response.json()["detail"]


class TestRestoreRecordsRoute:
    def test_passes_the_ids_and_returns_each_outcome(self) -> None:
        app, kb, _ = _make_app()
        kb.restore_records = AsyncMock(return_value={
            "success": False, "code": 200, "restoredCount": 1, "failedCount": 1,
            "results": [{"recordId": "r1", "success": True}, {"recordId": "r2", "success": False, "code": 404}],
        })
        response = TestClient(app).post("/api/v1/kb/records/restore", json={"recordIds": ["r1", "r2"]})
        assert response.status_code == 200
        assert response.json()["failedCount"] == 1
        kb.restore_records.assert_awaited_once_with(record_ids=["r1", "r2"], user_id="user1", org_id="org1")

    def test_a_malformed_body_says_what_to_send(self) -> None:
        app, kb, _ = _make_app()
        kb.restore_records = AsyncMock()
        for body in ({"recordIds": "r1"}, {"recordIds": [""]}, {}):
            response = TestClient(app).post("/api/v1/kb/records/restore", json=body)
            assert response.status_code == 400
            assert "recordIds" in response.json()["detail"]
        kb.restore_records.assert_not_called()

    def test_too_many_ids_are_refused_by_the_service(self) -> None:
        app, kb, _ = _make_app()
        kb.restore_records = AsyncMock(return_value={
            "success": False, "code": 400, "reason": "Choose between 1 and 100 items to restore.",
        })
        response = TestClient(app).post("/api/v1/kb/records/restore", json={"recordIds": ["r1"]})
        assert response.status_code == 400
        assert response.json()["detail"] == "Choose between 1 and 100 items to restore."
