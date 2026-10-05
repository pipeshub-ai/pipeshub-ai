"""The "Recently deleted" list of a collection (KnowledgeBaseService.list_trash and its route).

Who may see it (the roles that may restore), what a file organizer sees, the
org and user from the token, paging, and when an item may be removed for good.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.connectors.sources.localKB.handlers.kb_service import (
    MAX_TRASH_PAGE_SIZE,
    TRASH_NEEDS_EDIT_ACCESS_REASON,
    TRASH_TURNED_OFF_REASON,
    KnowledgeBaseService,
)
from tests.unit.connectors.sources.localKB.test_kb_router import _make_app

if TYPE_CHECKING:
    from collections.abc import Iterator

MODULE = "app.connectors.sources.localKB.handlers.kb_service"
KB = "kb-1"
ORG = "org-1"
DAY_MS = 24 * 60 * 60 * 1000
DELETED_AT = 1_790_000_000_000


def _row(key: str = "r1", *, is_file: bool = True, **extra) -> dict:
    row = {
        "record": {
            "_key": key,
            "recordName": "a.pdf" if is_file else "Docs",
            "recordType": "FILE",
            "mimeType": "application/pdf" if is_file else "application/vnd.folder",
            "deletedAtTimestamp": DELETED_AT,
        },
        "parentId": None,
        "parentName": None,
        "parentIsDeleted": None,
        "isFile": is_file,
        "fileMimeType": "application/pdf" if is_file else None,
        "sizeInBytes": 2048 if is_file else None,
        "batchSize": 1,
        "deletedByName": "Ada Admin",
        "deletedByEmail": "ada@acme.test",
    }
    row.update(extra)
    return row


@pytest.fixture(autouse=True)
def flag_on() -> Iterator[AsyncMock]:
    with patch(f"{MODULE}.is_soft_delete_enabled", AsyncMock(return_value=True)) as flag:
        yield flag


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOFT_DELETE_PURGE_MIN_AGE_SECONDS", raising=False)
    monkeypatch.delenv("SOFT_DELETE_PURGE_INTERVAL_SECONDS", raising=False)


@pytest.fixture
def svc(service: KnowledgeBaseService) -> KnowledgeBaseService:
    gp = service.graph_provider
    gp.get_user_by_user_id = AsyncMock(return_value={"id": "uk1", "_key": "uk1"})
    gp.get_user_kb_permission = AsyncMock(return_value="OWNER")
    gp.list_trashed_records = AsyncMock(return_value={"items": [_row()], "total": 1})
    service.config_service.get_config = AsyncMock(return_value={"softDeletePurge": {"minAgeDays": 20}})
    return service


class TestWhoSeesTheTrash:
    async def test_flag_off_refuses_with_the_way_to_turn_it_on(self, svc, flag_on) -> None:
        flag_on.return_value = False
        result = await svc.list_trash(KB, "u1", ORG)
        assert (result["code"], result["reason"]) == (403, TRASH_TURNED_OFF_REASON)
        svc.graph_provider.list_trashed_records.assert_not_called()

    async def test_someone_with_no_role_on_the_collection_is_told_it_is_not_found(self, svc) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
        result = await svc.list_trash(KB, "u1", ORG)
        assert result["code"] == 404
        svc.graph_provider.list_trashed_records.assert_not_called()

    @pytest.mark.parametrize("role", ["READER", "COMMENTER"])
    async def test_a_reader_cannot_see_what_they_could_not_restore(self, svc, role) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=role)
        result = await svc.list_trash(KB, "u1", ORG)
        assert (result["code"], result["reason"]) == (403, TRASH_NEEDS_EDIT_ACCESS_REASON)
        svc.graph_provider.list_trashed_records.assert_not_called()

    async def test_no_org_in_the_token_reads_as_not_found(self, svc) -> None:
        result = await svc.list_trash(KB, "u1", "")
        assert result["code"] == 404
        svc.graph_provider.list_trashed_records.assert_not_called()

    @pytest.mark.parametrize(("role", "single_only"), [("OWNER", False), ("WRITER", False), ("FILEORGANIZER", True)])
    async def test_a_file_organizer_sees_only_single_files(self, svc, role, single_only) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=role)
        result = await svc.list_trash(KB, "u1", ORG, page=3, limit=10)
        assert result["success"] is True
        svc.graph_provider.list_trashed_records.assert_awaited_once_with(
            KB, ORG, skip=20, limit=10, single_file_batches_only=single_only,
        )


class TestTheList:
    @pytest.mark.parametrize(("page", "limit"), [(0, 25), (1, 0), (1, MAX_TRASH_PAGE_SIZE + 1), (True, 25)])
    async def test_a_bad_page_is_refused_before_any_read(self, svc, page, limit) -> None:
        result = await svc.list_trash(KB, "u1", ORG, page=page, limit=limit)
        assert result["code"] == 400
        svc.graph_provider.get_user_by_user_id.assert_not_called()

    async def test_an_item_says_where_it_was_who_deleted_it_and_from_when_it_goes(self, svc) -> None:
        result = await svc.list_trash(KB, "u1", ORG)
        assert result["items"] == [{
            "id": "r1",
            "name": "a.pdf",
            "recordType": "FILE",
            "isFolder": False,
            "mimeType": "application/pdf",
            "sizeInBytes": 2048,
            "parentId": None,
            "parentName": None,
            "parentInTrash": False,
            "itemCount": 1,
            "deletedAtTimestamp": DELETED_AT,
            "deletedBy": {"name": "Ada Admin", "email": "ada@acme.test"},
            "removableAfterTimestamp": DELETED_AT + 20 * DAY_MS,
        }]
        assert result["retention"] == {"minAgeMs": 20 * DAY_MS}
        assert result["pagination"] == {"page": 1, "limit": 25, "totalCount": 1, "totalPages": 1}

    async def test_a_folder_in_a_trashed_folder_counts_its_contents(self, svc) -> None:
        svc.graph_provider.list_trashed_records = AsyncMock(return_value={"items": [
            _row("f1", is_file=False, batchSize=4, parentId="p1", parentName="Projects", parentIsDeleted=True,
                 deletedByName=None, deletedByEmail=None),
        ], "total": 51})
        result = await svc.list_trash(KB, "u1", ORG, page=2, limit=25)
        item = result["items"][0]
        assert (item["isFolder"], item["mimeType"], item["sizeInBytes"]) == (True, None, None)
        assert (item["itemCount"], item["parentName"], item["parentInTrash"]) == (4, "Projects", True)
        assert item["deletedBy"] is None
        assert result["pagination"]["totalPages"] == 3

    async def test_the_retention_defaults_to_fourteen_days(self, svc) -> None:
        svc.config_service.get_config = AsyncMock(return_value={})
        result = await svc.list_trash(KB, "u1", ORG)
        assert result["items"][0]["removableAfterTimestamp"] == DELETED_AT + 14 * DAY_MS

    async def test_unreadable_settings_leave_the_date_out_rather_than_guess(self, svc) -> None:
        svc.config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        result = await svc.list_trash(KB, "u1", ORG)
        assert result["success"] is True
        assert result["items"][0]["removableAfterTimestamp"] is None
        assert result["retention"] is None

    async def test_a_failed_read_says_so_in_plain_words(self, svc) -> None:
        svc.graph_provider.list_trashed_records = AsyncMock(side_effect=RuntimeError("graph at 10.0.0.1 down"))
        result = await svc.list_trash(KB, "u1", ORG)
        assert result["code"] == 500
        assert result["reason"].startswith("We couldn't load the recently deleted items.")
        assert "10.0.0.1" not in result["reason"]


class TestTheRoute:
    def test_lists_as_the_signed_in_user(self) -> None:
        app, kb, _ = _make_app()
        kb.list_trash = AsyncMock(return_value={
            "success": True, "code": 200,
            "items": [{"id": "r1", "name": "a.pdf", "isFolder": False, "parentInTrash": False, "itemCount": 1,
                       "deletedAtTimestamp": DELETED_AT}],
            "pagination": {"page": 2, "limit": 10, "totalCount": 11, "totalPages": 2},
            "retention": {"minAgeMs": 14 * DAY_MS},
        })
        response = TestClient(app).get(f"/api/v1/kb/{KB}/trash?page=2&limit=10&orgId=other-org&userId=other")
        assert response.status_code == 200
        assert response.json()["items"][0]["id"] == "r1"
        kb.list_trash.assert_awaited_once_with(kb_id=KB, user_id="user1", org_id="org1", page=2, limit=10)

    def test_a_refusal_keeps_its_status_and_words(self) -> None:
        app, kb, _ = _make_app()
        kb.list_trash = AsyncMock(return_value={"success": False, "code": 403, "reason": TRASH_NEEDS_EDIT_ACCESS_REASON})
        response = TestClient(app).get(f"/api/v1/kb/{KB}/trash")
        assert response.status_code == 403
        assert response.json()["detail"] == TRASH_NEEDS_EDIT_ACCESS_REASON

    @pytest.mark.parametrize("query", ["page=0", "limit=0", "limit=101", "page=abc"])
    def test_a_bad_page_never_reaches_the_service(self, query) -> None:
        app, kb, _ = _make_app()
        kb.list_trash = AsyncMock()
        response = TestClient(app).get(f"/api/v1/kb/{KB}/trash?{query}")
        assert response.status_code == 422
        kb.list_trash.assert_not_called()

    def test_an_unexpected_error_does_not_leak(self) -> None:
        app, kb, _ = _make_app()
        kb.list_trash = AsyncMock(side_effect=RuntimeError("graph down at 10.0.0.1"))
        response = TestClient(app).get(f"/api/v1/kb/{KB}/trash")
        assert response.status_code == 500
        assert "10.0.0.1" not in response.json()["detail"]
