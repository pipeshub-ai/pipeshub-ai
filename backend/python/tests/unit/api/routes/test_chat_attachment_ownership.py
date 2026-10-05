"""The chat attachment share, unshare and delete routes take record and user ids
from the client, and a READER edge they write is honoured by every permission
check: only the uploader may act, only on chat attachments of their org, and
only for users of that org. Share and unshare name the uploader through the
service token Node signs; delete takes the caller from the request."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes.chatbot import (
    delete_chat_attachment,
    grant_attachment_permissions,
    revoke_attachment_permissions,
)
from app.config.constants.arangodb import Connectors

ORG = "org-1"


def _attachment(org: str = ORG) -> dict:
    return {
        "orgId": org,
        "origin": "UPLOAD",
        "connectorId": f"attachments_{org}",
        "connectorName": Connectors.ATTACHMENTS.value,
    }


def _graph(records: dict, owned: set, users: dict, readers: frozenset = frozenset()) -> AsyncMock:
    """``records``: id -> document; ``owned``: record ids the caller (key "k-me")
    uploaded; ``users``: auth userId -> user document; ``readers``: existing
    (user key, record id) READER edges."""
    graph = AsyncMock()
    graph.get_document = AsyncMock(side_effect=lambda rid, _collection: records.get(rid))

    def edge(*, from_id, to_id, **_):
        if from_id == "k-me" and to_id in owned:
            return {"role": "OWNER"}
        return {"role": "READER"} if (from_id, to_id) in readers else None

    graph.get_edge = AsyncMock(side_effect=edge)
    graph.get_user_by_user_id = AsyncMock(side_effect=lambda uid: users.get(uid))
    return graph


USERS = {
    "me": {"_key": "k-me", "orgId": ORG},
    "colleague": {"_key": "k-col", "orgId": ORG},
    "outsider": {"_key": "k-out", "orgId": "org-2"},
}


CLAIMS = {"orgId": ORG, "userId": "me"}


def _request(body: dict | None = None, **user) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body or {})
    request.state.user = {"orgId": ORG, "userId": "me", **user}
    return request


@pytest.mark.asyncio
async def test_grant_writes_readers_only_on_own_attachments_for_org_users() -> None:
    records = {
        "mine": _attachment(),
        "theirs": _attachment(),
        "kb-file": {"orgId": ORG, "origin": "UPLOAD", "connectorId": "knowledgeBase_org-1"},
        "connector": {"orgId": ORG, "origin": "CONNECTOR", "connectorId": "gdrive-1"},
    }
    graph = _graph(records, owned={"mine", "kb-file", "connector"}, users=USERS)
    body = {"userIds": ["colleague", "outsider", "me"], "recordIds": list(records)}

    out = await grant_attachment_permissions(_request(body), graph, CLAIMS)

    edges = graph.batch_create_edges.await_args.args[0]
    assert {(e["from_id"], e["to_id"], e["role"]) for e in edges} == {("k-col", "mine", "READER")}
    assert out == {"granted": 1}


@pytest.mark.asyncio
async def test_grant_for_a_grantor_who_uploaded_nothing_writes_nothing() -> None:
    graph = _graph({"theirs": _attachment()}, owned=set(), users=USERS)
    body = {"userIds": ["colleague"], "recordIds": ["theirs"]}

    out = await grant_attachment_permissions(_request(body), graph, CLAIMS)

    assert out == {"granted": 0}
    graph.batch_create_edges.assert_not_called()


@pytest.mark.asyncio
async def test_revoke_never_removes_the_uploaders_own_edge() -> None:
    graph = _graph({"mine": _attachment(), "theirs": _attachment()}, owned={"mine"}, users=USERS,
                   readers=frozenset({("k-col", "mine"), ("k-col", "theirs")}))
    body = {"userIds": ["colleague", "me"], "recordIds": ["mine", "theirs"]}

    out = await revoke_attachment_permissions(_request(body), graph, CLAIMS)

    edges = graph.batch_delete_edges.await_args.args[0]
    assert [(e["from_id"], e["to_id"]) for e in edges] == [("k-col", "mine")]
    assert out == {"revoked": 1}


# A refused delete answers 404 whatever the reason, so the id is not confirmed.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        {"orgId": ORG, "origin": "UPLOAD", "connectorId": "kb", "connectorName": "KB"},
        {**_attachment(), "connectorName": "KB"},
        {**_attachment(), "connectorId": "kb"},
        {**_attachment(), "origin": "CONNECTOR"},
    ],
    ids=["kb-file", "wrong-connector-name", "wrong-connector-id", "wrong-origin"],
)
async def test_delete_refuses_a_record_that_is_not_a_chat_attachment(record: dict) -> None:
    graph = _graph({"not-an-attachment": record}, owned={"not-an-attachment"}, users=USERS)
    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment("not-an-attachment", _request(), graph)
    assert exc.value.status_code == 404
    graph.delete_nodes_and_edges.assert_not_called()


@pytest.mark.asyncio
async def test_delete_refuses_another_users_attachment() -> None:
    graph = _graph({"theirs": _attachment()}, owned=set(), users=USERS)
    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment("theirs", _request(), graph)
    assert exc.value.status_code == 404
    graph.delete_nodes_and_edges.assert_not_called()


@pytest.mark.asyncio
async def test_delete_answers_alike_for_every_id_that_is_not_the_callers() -> None:
    """A different status for "no such record" than for "not yours" would tell
    any member which record ids exist, in their org or another."""
    records = {
        "theirs": _attachment(),
        "other-org": _attachment("org-2"),
        "kb-file": {"orgId": ORG, "origin": "UPLOAD", "connectorId": "kb", "connectorName": "KB"},
    }
    graph = _graph(records, owned={"other-org", "kb-file"}, users=USERS)

    answers = set()
    for record_id in ("missing", *records):
        with pytest.raises(HTTPException) as exc:
            await delete_chat_attachment(record_id, _request(), graph)
        answers.add((exc.value.status_code, exc.value.detail))

    assert answers == {(404, "Attachment not found")}
    graph.delete_nodes_and_edges.assert_not_called()
    graph.delete_nodes.assert_not_called()


@pytest.mark.asyncio
async def test_the_uploader_deletes_their_attachment() -> None:
    graph = _graph({"mine": _attachment()}, owned={"mine"}, users=USERS)
    await delete_chat_attachment("mine", _request(), graph)
    graph.delete_nodes_and_edges.assert_awaited_once()
    graph.delete_nodes.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_service_account_is_refused() -> None:
    graph = _graph({"svc-upload": _attachment()}, owned={"svc-upload"}, users=USERS)
    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment("svc-upload", _request(isServiceAccount=True), graph)
    assert exc.value.status_code == 404
    graph.delete_nodes_and_edges.assert_not_called()
