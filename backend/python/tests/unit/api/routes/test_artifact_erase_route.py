"""E7 (PA-5): the query service's artifact erase route, for Node's erasure job only."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.middlewares.auth import require_service_token
from app.config.constants.service import TokenScopes
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from app.services.chat_attachments.uploads import Uploader, UploaderKind
from tests.support.bound_org import authed
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider


def _request(body: object) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = MagicMock()
    return request


def _token_request(user: dict[str, Any] | None) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


async def _two_conversations() -> tuple[FakeGraphProvider, FakeBlobStore]:
    graph, blob = FakeGraphProvider(), FakeBlobStore()
    graph.add_user("u1")
    service = ArtifactRegistryService(graph, blob)
    for conversation, org in (("conv-a", "org-1"), ("conv-a", "org-2"), ("conv-b", "org-1")):
        await service.register(
            actor=Actor(org_id=org, user_id="u1"),
            name=f"{conversation}-{org}.txt",
            artifact_type=ArtifactType.DOCUMENT,
            mime_type="text/plain",
            content=b"x",
            conversation_id=conversation,
        )
    return graph, blob


@pytest.mark.parametrize(
    "user",
    [
        None,
        {"token_type": "regular", "orgId": "org-1", "userId": "u1"},
        {"token_type": "scoped", "orgId": "org-1", "scopes": ["conversation:permissions"]},
        {"token_type": "scoped", "orgId": "org-1", "scopes": ["storage:token"]},
    ],
)
async def test_only_a_service_token_scoped_conversation_erase_passes(user: dict[str, Any] | None) -> None:
    check = require_service_token(TokenScopes.CONVERSATION_ERASE)
    with pytest.raises(HTTPException) as refused:
        await check(_token_request(user))
    assert refused.value.status_code == (401 if user is None else 403)


async def test_a_conversation_erase_token_passes() -> None:
    check = require_service_token(TokenScopes.CONVERSATION_ERASE)
    user = {"token_type": "scoped", "orgId": "org-1", "scopes": ["conversation:erase"]}
    assert await check(_token_request(user)) == user


async def test_erases_the_named_conversation_of_the_tokens_org_only() -> None:
    from app.api.routes.chatbot import erase_artifacts

    graph, blob = await _two_conversations()
    with patch("app.api.routes.chatbot.BlobStorage", return_value=blob):
        out = await authed(erase_artifacts)(
            _request({"conversationId": "conv-a"}), graph, MagicMock(), {"orgId": "org-1"}
        )
    assert out == {"artifacts": 1, "blobs": 1, "uploads": 0, "skillCandidates": 0, "learnedSkills": 0, "remaining": False}
    left = sorted((doc["orgId"], doc["conversationId"]) for doc in graph.nodes["artifacts"].values())
    assert left == [("org-1", "conv-b"), ("org-2", "conv-a")]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"conversationId": "conv-a", "discardedBefore": 5},
        {"conversationId": ""},
        {"conversationId": "conv-a", "orgId": "org-2"},
        {"discardedBefore": -1},
    ],
)
async def test_refuses_a_body_that_does_not_name_exactly_one_thing(body: dict[str, Any]) -> None:
    from app.api.routes.chatbot import erase_artifacts

    with pytest.raises(HTTPException) as refused:
        await authed(erase_artifacts)(_request(body), FakeGraphProvider(), MagicMock(), {"orgId": "org-1"})
    assert refused.value.status_code == 400


async def test_refuses_a_token_without_an_org() -> None:
    from app.api.routes.chatbot import erase_artifacts

    with pytest.raises(HTTPException) as refused:
        await authed(erase_artifacts)(_request({"conversationId": "c"}), FakeGraphProvider(), MagicMock(), {})
    assert refused.value.status_code == 400


def _attachment(
    graph: FakeGraphProvider, blob: FakeBlobStore, record_id: str, *, org: str, owner: str | None,
    uploader: Uploader | None = None,
) -> None:
    """An upload as `upload_chat_attachments` leaves it: record, file, owner edge, mapping, file and blocks.

    No ``uploader`` is an upload written before uploaders were recorded; no ``owner``, one without an OWNER edge.
    """
    vrid = f"vr-{record_id}"
    graph.nodes["records"][record_id] = {
        "_key": record_id, "id": record_id, "orgId": org, "connectorName": "ATTACHMENTS", "recordType": "FILE",
        "externalRecordId": blob.store(org, b"upload"), "virtualRecordId": vrid,
        **(uploader.record_fields() if uploader else {}),
    }
    graph.nodes["files"][record_id] = {"_key": record_id, "id": record_id, "orgId": org}
    graph.nodes["virtualRecordToDocIdMapping"][vrid] = {"_key": vrid, "id": vrid, "orgId": org}
    blob.store(org, b"blocks", virtual_record_id=vrid)
    if owner is not None:
        graph.edges["permission"].append({"from_id": owner, "to_id": record_id, "role": "OWNER", "type": "USER"})


async def test_qa15f_8_erases_the_tokens_orgs_attachments_their_sender_owns_and_nothing_else() -> None:
    from app.api.routes.chatbot import erase_attachments

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    graph.add_user("u1")
    graph.add_user("u2")
    _attachment(graph, blob, "mine", org="org-1", owner="u1")
    _attachment(graph, blob, "theirs", org="org-1", owner="u2")
    _attachment(graph, blob, "other-org", org="org-2", owner="u1")
    graph.add_record("kb-record", org_id="org-1")
    body = {"attachments": [{"recordId": rid, "ownerId": "u1"} for rid in ("mine", "theirs", "other-org", "kb-record", "gone")]}
    with patch("app.api.routes.chatbot.BlobStorage", return_value=blob):
        out = await authed(erase_attachments)(_request(body), graph, MagicMock(), {"orgId": "org-1"})
        again = await authed(erase_attachments)(_request(body), graph, MagicMock(), {"orgId": "org-1"})
    assert out == {"attachments": 1, "blobs": 2, "refused": 3, "unrecorded": 1, "removed": ["mine", "gone"]}
    assert again == {"attachments": 0, "blobs": 0, "refused": 3, "unrecorded": 1, "removed": ["mine", "gone"]}
    assert sorted(graph.nodes["records"]) == ["kb-record", "other-org", "theirs"]
    assert sorted(graph.nodes["files"]) == ["other-org", "theirs"]
    assert sorted(graph.nodes["virtualRecordToDocIdMapping"]) == ["vr-other-org", "vr-theirs"]
    assert len(blob.documents) == 4


async def test_qa15g_3_the_recorded_uploader_decides_and_an_unrecorded_upload_needs_the_owner_edge() -> None:
    from app.api.routes.chatbot import erase_attachments

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    graph.add_user("u1")
    graph.add_user("u2")
    person = Uploader("slack-person", UploaderKind.SERVICE_ACCOUNT, "slack-agent")
    _attachment(graph, blob, "service", org="org-1", owner=None, uploader=person)
    _attachment(graph, blob, "gone-user", org="org-1", owner=None, uploader=Uploader("u-gone", UploaderKind.PERSON))
    _attachment(graph, blob, "edge-held", org="org-1", owner="u1", uploader=Uploader("u2", UploaderKind.PERSON))
    _attachment(graph, blob, "legacy-mine", org="org-1", owner="u1")
    _attachment(graph, blob, "legacy-service", org="org-1", owner=None)

    def body(*refs: tuple[str, str]) -> dict[str, Any]:
        return {"attachments": [{"recordId": record_id, "ownerId": owner} for record_id, owner in refs]}

    with patch("app.api.routes.chatbot.BlobStorage", return_value=blob):
        others = await authed(erase_attachments)(
            _request(body(("service", "u1"), ("edge-held", "u1"), ("legacy-service", "slack-person"))),
            graph, MagicMock(), {"orgId": "org-1"},
        )
        theirs = await authed(erase_attachments)(
            _request(body(("service", "slack-person"), ("gone-user", "u-gone"), ("edge-held", "u2"), ("legacy-mine", "u1"))),
            graph, MagicMock(), {"orgId": "org-1"},
        )
    assert others == {"attachments": 0, "blobs": 0, "refused": 3, "unrecorded": 1, "removed": []}
    assert theirs == {
        "attachments": 4, "blobs": 8, "refused": 0, "unrecorded": 0,
        "removed": ["service", "gone-user", "edge-held", "legacy-mine"],
    }
    assert sorted(graph.nodes["records"]) == ["legacy-service"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"attachments": []},
        {"attachments": [{"recordId": "r", "ownerId": "u"}] * 51},
        {"attachments": [{"recordId": "", "ownerId": "u"}]},
        {"attachments": [{"recordId": "r"}]},
        {"attachments": [{"recordId": "r", "ownerId": "u"}], "orgId": "org-2"},
        {"attachments": [{"recordId": "r", "ownerId": "u", "orgId": "org-2"}]},
        ["r"],
    ],
)
async def test_qa15f_8_refuses_a_body_that_names_no_attachments_too_many_or_more_than_them(body: object) -> None:
    from app.api.routes.chatbot import erase_attachments

    with pytest.raises(HTTPException) as refused:
        await authed(erase_attachments)(_request(body), FakeGraphProvider(), MagicMock(), {"orgId": "org-1"})
    assert refused.value.status_code == 400


async def test_qa15f_8_refuses_a_token_without_an_org() -> None:
    from app.api.routes.chatbot import erase_attachments

    with pytest.raises(HTTPException) as refused:
        await authed(erase_attachments)(
            _request({"attachments": [{"recordId": "r", "ownerId": "u"}]}), FakeGraphProvider(), MagicMock(), {}
        )
    assert refused.value.status_code == 400
