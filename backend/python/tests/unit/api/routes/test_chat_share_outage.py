"""Share and unshare of chat attachments and artifacts when a graph read fails (API1-S2).

Node calls these routes after Mongo has changed who the conversation is shared with. A failed read used to read as "no
user" or "no edge" and the route answered 200 with a zero count, leaving the READER edge. A read the store could not
answer now fails the call with 503 and Retry-After, and nothing is written; any other failure is raised as is (500).
A stopped Neo4j or ArangoDB raises a connection error (ServiceUnavailable, aiohttp's ClientConnectorError), which
``error_class.retry_later`` counts as an outage; ``Outage`` stands for it here.

The fake follows each provider on a failed read: Neo4j and ArangoDB answer None unless ``raise_on_error=True`` is passed;
PostgreSQL's ``get_edge`` raises whatever the flag says.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import chatbot
from app.config.constants.arangodb import Connectors

ORG = "org-1"
CLAIMS = {"orgId": ORG, "userId": "owner"}
USERS = {"owner": {"_key": "k-owner", "orgId": ORG}, "bob": {"_key": "k-bob", "orgId": ORG}}
ATTACHMENT = {"orgId": ORG, "origin": "UPLOAD", "connectorId": f"attachments_{ORG}",
              "connectorName": Connectors.ATTACHMENTS.value}
# The owner OWNs rec-1; the share gave bob READER on it.
SHARED = {("k-owner", "rec-1"): "OWNER", ("k-bob", "rec-1"): "READER"}
NOT_YET_SHARED = {("k-owner", "rec-1"): "OWNER"}

ATT_BODY = {"userIds": ["bob"], "recordIds": ["rec-1"]}
ART_BODY = {"conversationId": "conv-1", "userIds": ["bob"]}


class Outage(ConnectionError):
    pass


def _graph(
    failing: frozenset = frozenset(), *, edges: dict = SHARED, postgres: bool = False, error: type = Outage,
) -> AsyncMock:
    """A store holding ``edges`` in which the reads named in ``failing`` fail: (method, key) where the key is the user
    id, the edge's user key, or the record id; ("get_documents_paginated", None) for the artifact listing."""

    def answer(name: str, key: str | None, value: Any, raise_on_error: bool) -> Any:  # noqa: ANN401 - each read's answer
        if (name, key) in failing:
            if raise_on_error or (postgres and name == "get_edge"):
                raise error(name)
            return None
        return value

    def edge(*, from_id: str, to_id: str, raise_on_error: bool = False, **_: object) -> dict | None:
        role = edges.get((from_id, to_id))
        return answer("get_edge", from_id, {"role": role} if role else None, raise_on_error)

    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(
        side_effect=lambda uid, *, raise_on_error=False: answer(
            "get_user_by_user_id", uid, USERS.get(uid), raise_on_error))
    gp.get_document = AsyncMock(
        side_effect=lambda rid, _c, *_, raise_on_error=False: answer("get_document", rid, ATTACHMENT, raise_on_error))
    gp.get_edge = AsyncMock(side_effect=edge)

    async def listing(*_: object, raise_on_error: bool = False, **__: object) -> list[dict]:
        return answer("get_documents_paginated", None, [{"_key": "rec-1"}], raise_on_error)

    gp.get_documents_paginated = AsyncMock(side_effect=listing)
    gp.batch_create_edges = AsyncMock()
    gp.batch_delete_edges = AsyncMock()
    return gp


def _req(body: dict) -> MagicMock:
    r = MagicMock()
    r.json = AsyncMock(return_value=body)
    return r


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chatbot, "notify_principals_changed", AsyncMock())


def _readable(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST artifacts asks the viewer check first; that check's reads are not this item (API1-S2c)."""
    access = MagicMock()
    access.decide = AsyncMock(return_value={"rec-1": "readable"})
    monkeypatch.setattr(chatbot, "artifact_viewer_access", lambda _gp: access)


def _assert_503(exc: pytest.ExceptionInfo) -> None:
    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "5"}


UNSHARE_ATTACHMENT_READS = [
    ("get_user_by_user_id", "owner"),  # the grantor, resolved for the uploader check
    ("get_user_by_user_id", "bob"),
    ("get_document", "rec-1"),
    ("get_edge", "k-owner"),  # the grantor's OWNER edge
    ("get_edge", "k-bob"),  # bob's READER edge
]


@pytest.mark.asyncio
@pytest.mark.parametrize("read", UNSHARE_ATTACHMENT_READS, ids=lambda r: f"{r[0]}[{r[1]}]")
async def test_unshare_attachment_with_a_failing_read_is_a_503_and_deletes_nothing(read) -> None:
    gp = _graph(frozenset({read}))
    with pytest.raises(HTTPException) as exc:
        await chatbot.revoke_attachment_permissions(_req(ATT_BODY), gp, CLAIMS)
    _assert_503(exc)
    gp.batch_delete_edges.assert_not_called()
    gp.batch_create_edges.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("read", [
    ("get_user_by_user_id", "owner"),
    ("get_user_by_user_id", "bob"),
    ("get_edge", "k-owner"),
    ("get_edge", "k-bob"),
    ("get_documents_paginated", None),
], ids=lambda r: f"{r[0]}[{r[1]}]")
async def test_unshare_artifacts_with_a_failing_read_is_a_503_and_deletes_nothing(read) -> None:
    gp = _graph(frozenset({read}))
    with pytest.raises(HTTPException) as exc:
        await chatbot.revoke_artifact_permissions(_req(ART_BODY), gp, CLAIMS)
    _assert_503(exc)
    gp.batch_delete_edges.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("read", UNSHARE_ATTACHMENT_READS,
                         ids=lambda r: f"{r[0]}[{r[1]}]")
async def test_share_attachment_with_a_failing_read_is_a_503_and_grants_nothing(read) -> None:
    gp = _graph(frozenset({read}), edges=NOT_YET_SHARED)
    with pytest.raises(HTTPException) as exc:
        await chatbot.grant_attachment_permissions(_req(ATT_BODY), gp, CLAIMS)
    _assert_503(exc)
    gp.batch_create_edges.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("read", [
    ("get_user_by_user_id", "bob"),
    ("get_edge", "k-bob"),
    ("get_edge", "k-owner"),
    ("get_documents_paginated", None),
], ids=lambda r: f"{r[0]}[{r[1]}]")
async def test_share_artifacts_with_a_failing_read_is_a_503_and_grants_nothing(read, monkeypatch) -> None:
    _readable(monkeypatch)
    gp = _graph(frozenset({read}), edges=NOT_YET_SHARED)
    with pytest.raises(HTTPException) as exc:
        await chatbot.grant_artifact_permissions(_req(ART_BODY), gp, CLAIMS)
    _assert_503(exc)
    gp.batch_create_edges.assert_not_called()


@pytest.mark.asyncio
async def test_postgres_get_edge_that_raises_whatever_the_flag_is_a_503_not_a_500() -> None:
    gp = _graph(frozenset({("get_edge", "k-bob")}), postgres=True)
    with pytest.raises(HTTPException) as exc:
        await chatbot.revoke_attachment_permissions(_req(ATT_BODY), gp, CLAIMS)
    _assert_503(exc)


@pytest.mark.asyncio
async def test_a_failure_that_is_not_an_outage_is_raised_as_is_not_a_503() -> None:
    gp = _graph(frozenset({("get_edge", "k-bob")}), error=KeyError)
    with pytest.raises(KeyError):
        await chatbot.revoke_attachment_permissions(_req(ATT_BODY), gp, CLAIMS)
    gp.batch_delete_edges.assert_not_called()


@pytest.mark.asyncio
async def test_a_healthy_unshare_still_revokes() -> None:
    gp = _graph()
    assert await chatbot.revoke_attachment_permissions(_req(ATT_BODY), gp, CLAIMS) == {"revoked": 1}
    assert await chatbot.revoke_artifact_permissions(_req(ART_BODY), _graph(), CLAIMS) == {"revoked": 1}


@pytest.mark.asyncio
async def test_a_user_who_really_is_not_there_is_still_skipped_not_a_503() -> None:
    gp = _graph()
    body = {"userIds": ["ghost"], "recordIds": ["rec-1"]}
    assert await chatbot.revoke_attachment_permissions(_req(body), gp, CLAIMS) == {"revoked": 0}
    assert await chatbot.revoke_artifact_permissions(
        _req({"conversationId": "conv-1", "userIds": ["ghost"]}), gp, CLAIMS) == {"revoked": 0}
    gp.batch_delete_edges.assert_not_called()


@pytest.mark.asyncio
async def test_deleting_an_attachment_whose_ownership_read_fails_is_a_503_not_a_404() -> None:
    # The route's own record read and the caller read succeed (else it is a 404 before the ownership check: C-28).
    gp = _graph(frozenset({("get_edge", "k-owner")}))
    gp.delete_nodes_and_edges = AsyncMock()
    request = MagicMock()
    request.state.user = {"orgId": ORG, "userId": "owner"}
    with pytest.raises(HTTPException) as exc:
        await chatbot.delete_chat_attachment("rec-1", request, gp, MagicMock())
    _assert_503(exc)
    gp.delete_nodes_and_edges.assert_not_called()
