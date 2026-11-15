"""The "Recently deleted" list of a collection (KnowledgeBaseService.list_trash and its route).

Who may see it (the roles that may restore), what a file organizer sees, the
org and user from the token, paging, and when an item may be removed for good.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.connectors.sources.localKB.handlers.kb_service import (
    MAX_TRASH_PAGE_SIZE,
    PAGE_BEYOND_LIMIT,
    TRASH_CURSOR_REFUSED_REASON,
    TRASH_NEEDS_EDIT_ACCESS_REASON,
    TRASH_PAGE_BEYOND_LIMIT_REASON,
    TRASH_TOTAL_CAP,
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

    # FILEORGANIZER is a retired role and reads as READER.
    @pytest.mark.parametrize("role", ["READER", "COMMENTER", "FILEORGANIZER"])
    async def test_a_reader_cannot_see_what_they_could_not_restore(self, svc, role) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=role)
        result = await svc.list_trash(KB, "u1", ORG)
        assert (result["code"], result["reason"]) == (403, TRASH_NEEDS_EDIT_ACCESS_REASON)
        svc.graph_provider.list_trashed_records.assert_not_called()

    async def test_no_org_in_the_token_reads_as_not_found(self, svc) -> None:
        result = await svc.list_trash(KB, "u1", "")
        assert result["code"] == 404
        svc.graph_provider.list_trashed_records.assert_not_called()

    @pytest.mark.parametrize(("role", "single_only"), [("OWNER", False), ("WRITER", False)])
    async def test_an_editor_sees_every_deleted_item(self, svc, role, single_only) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=role)
        result = await svc.list_trash(KB, "u1", ORG, page=3, limit=10)
        assert result["success"] is True
        svc.graph_provider.list_trashed_records.assert_awaited_once_with(
            KB, ORG, skip=20, limit=11, single_file_batches_only=single_only, after=None, count_cap=TRASH_TOTAL_CAP,
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
            "rootCount": 1,
            "otherRootNames": [],
            "deletedAtTimestamp": DELETED_AT,
            "deletedBy": {"name": "Ada Admin", "email": "ada@acme.test"},
            "removableAfterTimestamp": DELETED_AT + 20 * DAY_MS,
        }]
        assert result["retention"] == {"minAgeMs": 20 * DAY_MS}
        assert result["pagination"] == {
            "page": 1, "limit": 25, "totalCount": 1, "totalPages": 1, "totalCapped": False, "nextCursor": None,
        }

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

    async def test_a_multi_select_delete_is_one_item_counting_everything_it_restores(self, svc) -> None:
        svc.graph_provider.list_trashed_records = AsyncMock(return_value={"items": [
            _row("a", batchSize=6, rootCount=3, otherRootNames=["b.pdf", "Docs"]),
        ], "total": 1})
        [item] = (await svc.list_trash(KB, "u1", ORG))["items"]
        assert (item["id"], item["itemCount"], item["rootCount"], item["otherRootNames"]) == ("a", 6, 3, ["b.pdf", "Docs"])

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


class _Trash:
    """An in-memory trash answering ``list_trashed_records`` as the stores do: batches newest
    first by (deletedAt, batch id), ``after`` a keyset, ``skip`` on top, the count stopping at cap+1."""

    def __init__(self, batches: list[tuple[int, str, bool]]) -> None:
        self.batches = batches  # (deletedAt, batch id, a single file)
        self.calls: list[dict] = []

    async def __call__(self, connector_id, org_id, *, skip=0, limit=25, single_file_batches_only=False,
                       after=None, count_cap=None, transaction=None) -> dict:
        self.calls.append({"skip": skip, "limit": limit, "files": single_file_batches_only, "after": after})
        listed = sorted(
            (b for b in self.batches if b[2] or not single_file_batches_only), key=lambda b: (b[0], b[1]), reverse=True,
        )
        total = len(listed) if count_cap is None else min(len(listed), count_cap + 1)
        if after is not None:
            listed = [b for b in listed if (b[0], b[1]) < after]
        page = listed[skip:skip + limit]
        return {
            "items": [_row(f"r-{batch}", batchId=batch, batchDeletedAt=at, is_file=single) for at, batch, single in page],
            "total": total,
        }


def _cursor(**wire: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(wire).encode()).decode().rstrip("=")


class TestPagingByCursor:
    async def _walk(self, svc, limit: int, role: str = "OWNER") -> list[str]:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=role)
        seen: list[str] = []
        cursor, page = None, 1
        while True:
            result = await svc.list_trash(KB, "u1", ORG, page=page, limit=limit, cursor=cursor)
            assert result["success"] is True, result
            seen += [item["id"] for item in result["items"]]
            cursor = result["pagination"]["nextCursor"]
            if cursor is None:
                return seen
            page += 1

    async def test_following_next_cursor_lists_every_batch_once_in_order(self, svc) -> None:
        # Ties on the delete time are broken by batch id, so no tie is skipped or repeated at a page edge.
        batches = [(DELETED_AT - (i // 3) * 1000, f"b{i:03d}", i % 2 == 0) for i in range(53)]
        svc.graph_provider.list_trashed_records = _Trash(batches)
        expected = [f"r-{b}" for _, b, _ in sorted(batches, key=lambda b: (b[0], b[1]), reverse=True)]

        for limit in (1, 3, 7, 25, 53, 100):
            assert await self._walk(svc, limit) == expected

    async def test_a_page_after_the_cursor_is_read_from_the_cursor_not_from_the_page_number(self, svc) -> None:
        trash = _Trash([(DELETED_AT - i, f"b{i}", True) for i in range(5)])
        svc.graph_provider.list_trashed_records = trash
        first = await svc.list_trash(KB, "u1", ORG, page=1, limit=2)
        second = await svc.list_trash(KB, "u1", ORG, page=2, limit=2, cursor=first["pagination"]["nextCursor"])
        assert [i["id"] for i in second["items"]] == ["r-b2", "r-b3"]
        assert trash.calls[-1] == {"skip": 0, "limit": 3, "files": False, "after": (DELETED_AT - 1, "b1")}
        assert second["pagination"]["page"] == 2

    async def test_the_last_page_has_no_next_cursor_and_an_empty_trash_has_none_either(self, svc) -> None:
        svc.graph_provider.list_trashed_records = _Trash([(DELETED_AT, "b1", True), (DELETED_AT, "b2", True)])
        assert (await svc.list_trash(KB, "u1", ORG, limit=2))["pagination"]["nextCursor"] is None
        assert (await svc.list_trash(KB, "u1", ORG, limit=1))["pagination"]["nextCursor"] is not None
        svc.graph_provider.list_trashed_records = _Trash([])
        empty = await svc.list_trash(KB, "u1", ORG)
        assert (empty["items"], empty["pagination"]["nextCursor"], empty["pagination"]["totalCount"]) == ([], None, 0)

    async def test_a_batch_restored_between_pages_leaves_no_gap_and_a_new_delete_no_repeat(self, svc) -> None:
        trash = _Trash([(DELETED_AT - i, f"b{i}", True) for i in range(6)])
        svc.graph_provider.list_trashed_records = trash
        first = await svc.list_trash(KB, "u1", ORG, limit=2)
        trash.batches = [b for b in trash.batches if b[1] != "b1"] + [(DELETED_AT + 5, "new", True)]
        second = await svc.list_trash(KB, "u1", ORG, page=2, limit=2, cursor=first["pagination"]["nextCursor"])
        assert [i["id"] for i in second["items"]] == ["r-b2", "r-b3"]


class TestTheCap:
    @pytest.mark.parametrize(("page", "limit"), [(401, 25), (101, 100), (TRASH_TOTAL_CAP + 1, 1)])
    async def test_a_page_number_past_the_cap_is_refused_and_names_the_cursor(self, svc, page, limit) -> None:
        result = await svc.list_trash(KB, "u1", ORG, page=page, limit=limit)
        assert result == {
            "success": False, "code": 400, "errorCode": PAGE_BEYOND_LIMIT, "reason": TRASH_PAGE_BEYOND_LIMIT_REASON,
        }
        assert "nextCursor" in result["reason"]
        svc.graph_provider.list_trashed_records.assert_not_called()

    @pytest.mark.parametrize(("page", "limit"), [(400, 25), (100, 100), (TRASH_TOTAL_CAP, 1)])
    async def test_the_last_page_under_the_cap_is_still_served(self, svc, page, limit) -> None:
        result = await svc.list_trash(KB, "u1", ORG, page=page, limit=limit)
        assert result["success"] is True
        assert svc.graph_provider.list_trashed_records.await_args.kwargs["skip"] == (page - 1) * limit

    async def test_a_cursor_reaches_past_the_cap(self, svc) -> None:
        cursor = _cursor(kb=KB, f=False, t=DELETED_AT, b="b1")
        result = await svc.list_trash(KB, "u1", ORG, page=TRASH_TOTAL_CAP, limit=100, cursor=cursor)
        assert result["success"] is True
        assert svc.graph_provider.list_trashed_records.await_args.kwargs["after"] == (DELETED_AT, "b1")

    @pytest.mark.parametrize(("counted", "total", "capped", "pages"), [
        (TRASH_TOTAL_CAP - 1, TRASH_TOTAL_CAP - 1, False, 400),
        (TRASH_TOTAL_CAP, TRASH_TOTAL_CAP, False, 400),
        (TRASH_TOTAL_CAP + 1, TRASH_TOTAL_CAP, True, 400),
    ])
    async def test_the_total_stops_at_the_cap_and_says_so(self, svc, counted, total, capped, pages) -> None:
        svc.graph_provider.list_trashed_records = AsyncMock(return_value={"items": [_row()], "total": counted})
        pagination = (await svc.list_trash(KB, "u1", ORG))["pagination"]
        assert (pagination["totalCount"], pagination["totalCapped"], pagination["totalPages"]) == (total, capped, pages)
        assert svc.graph_provider.list_trashed_records.await_args.kwargs["count_cap"] == TRASH_TOTAL_CAP


class TestABadCursor:
    @pytest.mark.parametrize("cursor", [
        "not a cursor",
        "%%%",
        "é",
        _cursor(kb="kb-2", f=False, t=DELETED_AT, b="b1"),          # another collection
        _cursor(kb=KB, f=True, t=DELETED_AT, b="b1"),               # a file organizer's list
        _cursor(kb=KB, f=False, t=str(DELETED_AT), b="b1"),
        _cursor(kb=KB, f=False, t=True, b="b1"),
        _cursor(kb=KB, f=False, t=DELETED_AT, b=""),
        _cursor(kb=KB, f=False, t=DELETED_AT),
        _cursor(kb=KB, f=0, t=DELETED_AT, b="b1"),
        _cursor(kb=KB, f=False, t=2**63, b="b1"),                    # past what the stores hold: a 400, not a failed read
        _cursor(kb=KB, f=False, t=-1, b="b1"),
        _cursor(kb=KB, f=False, t=DELETED_AT, b="b\u0000"),
        base64.urlsafe_b64encode(b"[1, 2]").decode(),
    ])
    async def test_is_refused_never_read_as_the_first_page(self, svc, cursor) -> None:
        result = await svc.list_trash(KB, "u1", ORG, cursor=cursor)
        assert result == {"success": False, "code": 400, "reason": TRASH_CURSOR_REFUSED_REASON}
        svc.graph_provider.list_trashed_records.assert_not_called()

    @pytest.mark.parametrize("cursor", ["", "x" * 1025])
    async def test_an_empty_or_oversized_cursor_is_refused_before_any_read(self, svc, cursor) -> None:
        result = await svc.list_trash(KB, "u1", ORG, cursor=cursor)
        assert result["code"] == 400
        svc.graph_provider.get_user_by_user_id.assert_not_called()


class TestAFileOrganizersCursor:
    """Only single files for a role that may not restore a whole batch, cursor or not."""

    @pytest.fixture(autouse=True)
    def writer_restores_files_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(f"{MODULE}.RESTORE_BATCH_ROLES", ("OWNER",))

    async def test_an_owners_cursor_is_refused(self, svc) -> None:
        svc.graph_provider.list_trashed_records = _Trash([(DELETED_AT - i, f"b{i}", i % 2 == 0) for i in range(6)])
        owners = (await svc.list_trash(KB, "u1", ORG, limit=2))["pagination"]["nextCursor"]
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        assert await svc.list_trash(KB, "u1", ORG, page=2, limit=2, cursor=owners) == {
            "success": False, "code": 400, "reason": TRASH_CURSOR_REFUSED_REASON,
        }

    async def test_their_own_cursor_still_lists_single_files_only(self, svc) -> None:
        trash = _Trash([(DELETED_AT - i, f"b{i}", i % 2 == 0) for i in range(9)])
        svc.graph_provider.list_trashed_records = trash
        seen = await TestPagingByCursor()._walk(svc, 2, role="WRITER")
        assert seen == ["r-b0", "r-b2", "r-b4", "r-b6", "r-b8"]
        assert {call["files"] for call in trash.calls} == {True}


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
        kb.list_trash.assert_awaited_once_with(kb_id=KB, user_id="user1", org_id="org1", page=2, limit=10, cursor=None)

    def test_passes_the_cursor_and_the_capped_paging_on(self) -> None:
        app, kb, _ = _make_app()
        kb.list_trash = AsyncMock(return_value={
            "success": True, "code": 200, "items": [],
            "pagination": {"page": 401, "limit": 25, "totalCount": TRASH_TOTAL_CAP, "totalPages": 400,
                           "totalCapped": True, "nextCursor": "next"},
        })
        response = TestClient(app).get(f"/api/v1/kb/{KB}/trash?page=401&cursor=abc")
        assert response.status_code == 200
        assert (response.json()["pagination"]["totalCapped"], response.json()["pagination"]["nextCursor"]) == (True, "next")
        assert kb.list_trash.await_args.kwargs["cursor"] == "abc"

    def test_a_page_past_the_cap_answers_its_code(self) -> None:
        app, kb, _ = _make_app()
        kb.list_trash = AsyncMock(return_value={
            "success": False, "code": 400, "errorCode": PAGE_BEYOND_LIMIT, "reason": TRASH_PAGE_BEYOND_LIMIT_REASON,
        })
        response = TestClient(app).get(f"/api/v1/kb/{KB}/trash?page=401")
        assert response.status_code == 400
        assert response.json() == {"detail": TRASH_PAGE_BEYOND_LIMIT_REASON, "errorCode": PAGE_BEYOND_LIMIT}

    @pytest.mark.parametrize("cursor", ["", "x" * 1025])
    def test_an_empty_or_oversized_cursor_never_reaches_the_service(self, cursor) -> None:
        app, kb, _ = _make_app()
        kb.list_trash = AsyncMock()
        response = TestClient(app).get(f"/api/v1/kb/{KB}/trash?cursor={cursor}")
        assert response.status_code == 422
        kb.list_trash.assert_not_called()

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
