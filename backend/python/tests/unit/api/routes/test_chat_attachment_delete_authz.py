"""`DELETE /api/v1/chat/attachments/{record_id}` removes a record node and all its edges, and
the id comes from the client. It must not be a way around the rule on
`DELETE /api/v1/records/{id}`: only a chat attachment the caller owns may go.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (binds the edition seams before any router loads)
from app.api.routes.chatbot import delete_chat_attachment
from app.config.constants.arangodb import Connectors

ORG_A = "org-A"
ORG_B = "org-B"
RECORD_ID = "rec-1"
ATTACHMENT = {
    "orgId": ORG_A, "origin": "UPLOAD", "connectorName": Connectors.ATTACHMENTS.value,
    "connectorId": f"attachments_{ORG_A}",
}


def _request(org_id: str = ORG_A, user_id: str = "user-a") -> MagicMock:
    req = MagicMock()
    req.state.user = {"orgId": org_id, "userId": user_id}
    return req


def _provider(record: dict | None, permission: dict | None = None, user: dict | None = None) -> MagicMock:
    provider = MagicMock()
    provider.get_document = AsyncMock(return_value=record)
    provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-a"} if user is None else user)
    provider.get_edge = AsyncMock(return_value=permission)
    provider.delete_nodes_and_edges = AsyncMock()
    provider.delete_nodes = AsyncMock()
    return provider


def _assert_untouched(provider: MagicMock) -> None:
    provider.delete_nodes_and_edges.assert_not_awaited()
    provider.delete_nodes.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        {"orgId": ORG_A, "origin": "CONNECTOR", "connectorName": "DRIVE", "connectorId": "conn-1"},
        {"orgId": ORG_A, "origin": "CONNECTOR", "connectorName": "CONFLUENCE", "connectorId": "conn-1"},
        {"orgId": ORG_A, "origin": "UPLOAD", "connectorName": "KB", "connectorId": "kb-1"},
        {"orgId": ORG_A, "origin": "UPLOAD", "connectorName": "CODING_SANDBOX"},
        {"orgId": ORG_A, "origin": "UPLOAD"},
    ],
    ids=["drive", "confluence", "kb-upload", "artifact", "legacy-no-connector-name"],
)
async def test_other_kinds_of_record_are_not_removed_even_by_their_owner(record: dict) -> None:
    provider = _provider(record, permission={"role": "OWNER"})

    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment(RECORD_ID, _request(), provider)

    assert exc.value.status_code == 404
    _assert_untouched(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission",
    [None, {"role": "READER"}, {"role": "WRITER"}, {"role": "owner"}, {}],
    ids=["no-permission", "reader", "writer", "wrong-case", "no-role"],
)
async def test_an_attachment_the_caller_does_not_own_is_not_removed(permission: dict | None) -> None:
    provider = _provider(ATTACHMENT, permission=permission)

    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment(RECORD_ID, _request(), provider)

    assert exc.value.status_code == 404
    _assert_untouched(provider)


@pytest.mark.asyncio
async def test_a_caller_with_no_user_in_the_graph_removes_nothing() -> None:
    provider = _provider(ATTACHMENT, permission={"role": "OWNER"}, user={})
    provider.get_user_by_user_id = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment(RECORD_ID, _request(), provider)

    assert exc.value.status_code == 404
    _assert_untouched(provider)


@pytest.mark.asyncio
async def test_a_request_without_a_user_removes_nothing() -> None:
    provider = _provider(ATTACHMENT, permission={"role": "OWNER"})

    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment(RECORD_ID, _request(user_id=""), provider)

    assert exc.value.status_code == 404
    _assert_untouched(provider)


@pytest.mark.asyncio
async def test_another_orgs_attachment_is_refused() -> None:
    provider = _provider(
        {**ATTACHMENT, "orgId": ORG_B, "connectorId": f"attachments_{ORG_B}"}, permission={"role": "OWNER"}
    )

    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment(RECORD_ID, _request(), provider)

    # The same answer as a missing id, so another org's record id is never confirmed.
    assert exc.value.status_code == 404
    _assert_untouched(provider)


@pytest.mark.asyncio
async def test_a_request_without_org_context_is_refused() -> None:
    provider = _provider(ATTACHMENT, permission={"role": "OWNER"})

    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment(RECORD_ID, _request(org_id=""), provider)

    assert exc.value.status_code == 400
    _assert_untouched(provider)


@pytest.mark.asyncio
async def test_the_owner_removes_their_own_attachment() -> None:
    provider = _provider(ATTACHMENT, permission={"role": "OWNER"})

    await delete_chat_attachment(RECORD_ID, _request(), provider)

    provider.get_edge.assert_awaited_once()
    assert provider.get_edge.await_args.kwargs["from_id"] == "user-key-a"
    assert provider.get_edge.await_args.kwargs["to_id"] == RECORD_ID
    provider.delete_nodes_and_edges.assert_awaited_once()
    provider.delete_nodes.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_attachment_that_is_already_gone_is_not_found() -> None:
    provider = _provider(None)

    with pytest.raises(HTTPException) as exc:
        await delete_chat_attachment(RECORD_ID, _request(), provider)

    assert exc.value.status_code == 404
    _assert_untouched(provider)
