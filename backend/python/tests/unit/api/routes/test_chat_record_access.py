"""Shared chats are rendered per viewer: Node asks which records the viewer may
read (`/chat/records/access`) and, for an approved access request, has the owner
grant READER on a native record (`/chat/records/access-grant`). A connector
record is never granted here."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.chatbot import check_viewer_record_access, grant_record_access
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError

ORG = "org-1"
VIEWER = {"orgId": ORG, "userId": "viewer"}
OWNER = {"orgId": ORG, "userId": "owner"}


def _request(body: dict) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


def _graph(records: dict, owned: set[str] = frozenset(), kb_owner: str | None = None) -> AsyncMock:
    users = {"owner": {"_key": "k-owner", "userId": "owner", "orgId": ORG},
             "viewer": {"_key": "k-view", "userId": "viewer", "orgId": ORG}}
    graph = AsyncMock()

    async def get_document(key: str, collection: str) -> dict | None:
        if collection == "users":
            return users_by_key.get(key)
        if collection == "apps":
            return APPS.get(key)
        return records.get(key)

    users_by_key = {u["_key"]: u for u in users.values()}
    graph.get_document = AsyncMock(side_effect=get_document)
    graph.get_user_by_user_id = AsyncMock(side_effect=lambda uid, **_: users.get(uid))
    graph.get_edges_to_node = AsyncMock(
        side_effect=lambda node, _c, *a, **k: [{"_from": "users/k-owner", "role": "OWNER"}]
    )
    graph.list_kb_permissions = AsyncMock(
        return_value=[{"userId": "owner", "role": "OWNER"}, {"userId": "x", "role": "READER"}]
    )
    graph.get_user_kb_permission = AsyncMock(side_effect=lambda kb, key: "OWNER" if key == kb_owner else "READER")
    graph.get_kb_permissions = AsyncMock(return_value={"users": {}, "teams": {}})
    graph.create_kb_permissions = AsyncMock(return_value={"success": True})
    graph.get_edge = AsyncMock(
        side_effect=lambda *, from_id, to_id, **_: {"role": "OWNER"} if from_id == "k-owner" and to_id in owned else None
    )
    return graph


APPS = {"kb-1": {"type": "KB", "orgId": ORG, "name": "Finance"}}
RECORDS = {
    "kb-file": {"orgId": ORG, "origin": "UPLOAD", "connectorId": "kb-1", "recordName": "Plan"},
    "attachment": {"orgId": ORG, "origin": "UPLOAD", "connectorId": f"attachments_{ORG}", "recordName": "a.pdf"},
    "wiki-page": {"orgId": ORG, "origin": "CONNECTOR", "connectorId": "conf-1", "recordName": "Wiki",
                  "webUrl": "https://wiki.example/p/1"},
    "other-org": {"orgId": "org-2", "origin": "UPLOAD"},
}


@pytest.mark.asyncio
async def test_access_check_splits_readable_from_denied_for_the_token_viewer() -> None:
    graph = _graph(RECORDS)
    with patch("app.api.routes.chatbot.accessible_node_ids", AsyncMock(return_value={"kb-file"})) as check:
        out = await check_viewer_record_access(
            _request({"recordIds": ["kb-file", "wiki-page", "kb-file"]}), graph, VIEWER
        )
    check.assert_awaited_once_with(graph, {"kb-file", "wiki-page"}, "viewer", ORG)
    assert out == {"accessible": ["kb-file"], "denied": ["wiki-page"]}


@pytest.mark.asyncio
async def test_access_check_reports_kind_and_native_owner_without_leaking_other_orgs() -> None:
    graph = _graph(RECORDS)
    with patch("app.api.routes.chatbot.accessible_node_ids", AsyncMock(return_value=set())):
        out = await check_viewer_record_access(
            _request({"recordIds": ["kb-file", "wiki-page", "other-org", "gone"], "includeOwners": True}),
            graph, VIEWER,
        )
    assert {d["recordId"]: d for d in out["deniedDetails"]} == {
        "kb-file": {"recordId": "kb-file", "kind": "native", "recordName": "Plan", "webUrl": None,
                    "collection": {"id": "kb-1", "name": "Finance"}, "ownerUserIds": ["owner"]},
        "wiki-page": {"recordId": "wiki-page", "kind": "connector", "recordName": "Wiki",
                      "webUrl": "https://wiki.example/p/1", "collection": None, "ownerUserIds": ["owner"]},
        "other-org": {"recordId": "other-org", "kind": "unknown"},
        "gone": {"recordId": "gone", "kind": "unknown"},
    }


@pytest.mark.asyncio
async def test_access_check_fails_closed_when_the_check_is_unavailable() -> None:
    with patch("app.api.routes.chatbot.accessible_node_ids",
               AsyncMock(side_effect=PermissionVerificationUnavailableError("down"))):
        with pytest.raises(HTTPException) as exc:
            await check_viewer_record_access(_request({"recordIds": ["kb-file"]}), _graph(RECORDS), VIEWER)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_collection_record_request_goes_to_the_collection_owners() -> None:
    graph = _graph(RECORDS)
    with patch("app.api.routes.chatbot.accessible_node_ids", AsyncMock(return_value=set())):
        out = await check_viewer_record_access(
            _request({"recordIds": ["kb-file"], "includeOwners": True}), graph, VIEWER
        )
    graph.list_kb_permissions.assert_awaited_once_with("kb-1")
    assert out["deniedDetails"][0]["ownerUserIds"] == ["owner"]
    assert out["deniedDetails"][0]["collection"] == {"id": "kb-1", "name": "Finance"}


@pytest.mark.asyncio
async def test_grant_makes_the_user_reader_of_the_whole_collection_when_approver_owns_it() -> None:
    graph = _graph(RECORDS, kb_owner="k-owner")
    out = await grant_record_access(_request({"userId": "viewer", "recordIds": ["kb-file", "kb-file"]}), graph, OWNER)
    graph.create_kb_permissions.assert_awaited_once_with(
        kb_id="kb-1", requester_id="owner", user_ids=["k-view"], team_ids=[], role="READER"
    )
    graph.batch_create_edges.assert_not_awaited()  # no stray record-level edge
    assert out == {"granted": 1}


@pytest.mark.asyncio
async def test_grant_refuses_when_approver_does_not_own_the_collection() -> None:
    graph = _graph(RECORDS, kb_owner=None)
    out = await grant_record_access(_request({"userId": "viewer", "recordIds": ["kb-file"]}), graph, OWNER)
    graph.create_kb_permissions.assert_not_awaited()
    assert out == {"granted": 0}


@pytest.mark.asyncio
async def test_grant_never_downgrades_an_existing_member() -> None:
    graph = _graph(RECORDS, kb_owner="k-owner")
    graph.get_kb_permissions = AsyncMock(return_value={"users": {"k-view": "WRITER"}, "teams": {}})
    out = await grant_record_access(_request({"userId": "viewer", "recordIds": ["kb-file"]}), graph, OWNER)
    graph.create_kb_permissions.assert_not_awaited()
    assert out == {"granted": 0}


@pytest.mark.asyncio
async def test_grant_writes_reader_on_an_owned_chat_attachment_and_skips_connectors() -> None:
    records = {**RECORDS}
    graph = _graph(records, owned={"attachment", "wiki-page"}, kb_owner="k-owner")
    out = await grant_record_access(
        _request({"userId": "viewer", "recordIds": ["attachment", "wiki-page", "other-org"]}), graph, OWNER
    )
    edges = graph.batch_create_edges.await_args.args[0]
    assert [(e["from_id"], e["to_id"], e["role"]) for e in edges] == [("k-view", "attachment", "READER")]
    graph.create_kb_permissions.assert_not_awaited()
    assert out == {"granted": 1}
