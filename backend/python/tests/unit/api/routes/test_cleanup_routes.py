"""CLEANUP-G5, CLEANUP-G7: what the chat routes leave behind.

- A conversation's erase also takes the uploads made into it that no message carried, and the skill candidates
  learned from it.
- Node's unsent-upload collector lists the org's old uploads awaiting a message, marks those a message names as
  sent, and erases the rest through the message-based erase. Both routes take Node's `conversation:erase` token.
- Removing an attachment chip, or an upload that fails, purges the stored bytes too.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import TokenScopes
from app.services.chat_attachments.uploads import (
    AWAITING_SEND_FIELD,
    UPLOAD_CONVERSATION_FIELD,
)
from tests.support.bound_org import authed
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

CANDIDATES = CollectionNames.AGENT_SKILL_CANDIDATES.value


def _request(body: object) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = MagicMock()
    return request


def _upload(
    graph: FakeGraphProvider, blob: FakeBlobStore, key: str, *, org: str, conversation: str | None, at: int = 1,
) -> None:
    vr = f"vr-{key}"
    graph.nodes["records"][key] = {
        "_key": key, "id": key, "orgId": org, "connectorName": "ATTACHMENTS", "connectorId": f"attachments_{org}",
        "origin": "UPLOAD", "recordType": "FILE", "uploaderUserId": "u1", "uploaderKind": "person",
        "createdAtTimestamp": at,
        "externalRecordId": blob.store(org, b"upload"), "virtualRecordId": vr, AWAITING_SEND_FIELD: True,
        **({UPLOAD_CONVERSATION_FIELD: conversation} if conversation else {}),
    }
    graph.nodes["files"][key] = {"_key": key, "id": key, "orgId": org}
    blob.store(org, b"blocks", virtual_record_id=vr)


async def _candidate(graph: FakeGraphProvider, org: str, key: str, session: str) -> None:
    await GraphSkillStore(graph, org, "u1").queue_candidate(  # type: ignore[arg-type]
        SkillCandidate(
            candidate_id=key, name=key, description="d", body="b", source_session_id=session,
            source_trajectory_summary="what the run did", created_at="now",
        )
    )


async def test_cleanup_g5_g7_a_conversations_erase_takes_its_unsent_uploads_and_skill_candidates() -> None:
    from app.api.routes.chatbot import erase_artifacts

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    _upload(graph, blob, "unsent", org="org-1", conversation="conv-a")
    _upload(graph, blob, "sent", org="org-1", conversation="conv-a")
    graph.nodes["records"]["sent"][AWAITING_SEND_FIELD] = False
    _upload(graph, blob, "other-chat", org="org-1", conversation="conv-b")
    _upload(graph, blob, "other-org", org="org-2", conversation="conv-a")
    await _candidate(graph, "org-1", "learned", "conv-a")
    await _candidate(graph, "org-1", "learned-elsewhere", "conv-b")
    await _candidate(graph, "org-2", "other-org-candidate", "conv-a")

    with patch("app.api.routes.chatbot.BlobStorage", return_value=blob):
        out = await authed(erase_artifacts)(_request({"conversationId": "conv-a"}), graph, MagicMock(), {"orgId": "org-1"})
        again = await authed(erase_artifacts)(_request({"conversationId": "conv-a"}), graph, MagicMock(), {"orgId": "org-1"})

    assert out == {"artifacts": 0, "blobs": 0, "uploads": 1, "skillCandidates": 1, "learnedSkills": 0, "remaining": False}
    assert again == {"artifacts": 0, "blobs": 0, "uploads": 0, "skillCandidates": 0, "learnedSkills": 0, "remaining": False}
    # A sent upload is the message-based erase's: it marks the upload removed in forks too.
    assert sorted(graph.nodes["records"]) == ["other-chat", "other-org", "sent"]
    assert sorted(graph.nodes[CANDIDATES]) == ["learned-elsewhere", "other-org-candidate"]
    assert len(blob.documents) == 6


def _token(user: dict[str, Any] | None) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


@pytest.mark.parametrize("route", ["mark_attachments_sent", "list_unsent_attachments"])
async def test_cleanup_g5_only_nodes_conversation_erase_service_token_may_call_the_collector_routes(route: str) -> None:
    from app.api.routes import chatbot

    check = inspect.signature(getattr(chatbot, route)).parameters["claims"].default.dependency
    erase = {"token_type": "scoped", "orgId": "org-1", "scopes": [TokenScopes.CONVERSATION_ERASE.value]}
    assert await check(_token(erase)) == erase
    for refused_user in (
        None,
        {"token_type": "regular", "orgId": "org-1", "userId": "u1"},
        {"token_type": "scoped", "orgId": "org-1", "scopes": [TokenScopes.CONVERSATION_CREATE.value]},
    ):
        with pytest.raises(HTTPException) as refused:
            await check(_token(refused_user))
        assert refused.value.status_code in (401, 403)


async def test_cleanup_g5_marking_sent_changes_only_the_tokens_orgs_uploads() -> None:
    from app.api.routes.chatbot import mark_attachments_sent

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    _upload(graph, blob, "mine", org="org-1", conversation=None)
    _upload(graph, blob, "theirs", org="org-2", conversation=None)
    out = await authed(mark_attachments_sent)(
        _request({"conversationId": "conv-1", "recordIds": ["mine", "theirs", "missing"]}), graph, {"orgId": "org-1"}
    )
    assert out == {"marked": ["mine"]}
    assert (graph.nodes["records"]["mine"][AWAITING_SEND_FIELD], graph.nodes["records"]["mine"][UPLOAD_CONVERSATION_FIELD]) == (
        False, "conv-1",
    )
    assert graph.nodes["records"]["theirs"][AWAITING_SEND_FIELD] is True


@pytest.mark.parametrize("body", [{}, {"recordIds": []}, {"recordIds": ["a"], "orgId": "org-2"}, {"recordIds": ["a"] * 51}])
async def test_cleanup_g5_a_sent_mark_names_one_to_fifty_uploads_and_never_the_org(body: dict[str, Any]) -> None:
    from app.api.routes.chatbot import mark_attachments_sent

    with pytest.raises(HTTPException) as refused:
        await authed(mark_attachments_sent)(_request(body), FakeGraphProvider(), {"orgId": "org-1"})
    assert refused.value.status_code == 400


async def test_cleanup_g5_removing_a_chip_purges_the_stored_bytes() -> None:
    from app.api.routes.chatbot import delete_chat_attachment

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    _upload(graph, blob, "mine", org="org-1", conversation=None)
    request = MagicMock()
    request.state.user = {"orgId": "org-1", "userId": "me"}
    request.app.container.logger.return_value = MagicMock()
    with (
        patch("app.api.routes.chatbot.BlobStorage", return_value=blob),
        patch("app.api.routes.chatbot._attachment_caller", AsyncMock(return_value=("org-1", "k-me", False))),
        patch("app.api.routes.chatbot._owned_chat_attachments", AsyncMock(return_value=["mine"])),
    ):
        await delete_chat_attachment("mine", request, graph, MagicMock())
    assert "mine" not in graph.nodes["records"] and "mine" not in graph.nodes["files"]
    assert blob.documents == {}


async def test_cleanup_g5_a_failed_upload_purges_what_it_stored() -> None:
    from app.api.routes.chatbot import _purge_stored_attachments

    blob = FakeBlobStore()
    first = blob.store("org-1", b"file one")
    blob.store("org-1", b"blocks one", virtual_record_id="vr-1")
    kept = blob.store("org-1", b"someone else's")
    blob.purge_error = None
    await _purge_stored_attachments(blob, "org-1", [(first, "vr-1"), ("", "vr-2")])  # type: ignore[arg-type]
    assert list(blob.documents) == [kept]

    blob.purge_error = RuntimeError("storage down")
    # Best-effort: a purge failure never replaces the upload's own error.
    await _purge_stored_attachments(blob, "org-1", [(kept, "vr-3")])  # type: ignore[arg-type]


async def test_cleanup_g5_listing_names_only_the_tokens_orgs_old_unsent_uploads_and_pages() -> None:
    from app.api.routes.chatbot import list_unsent_attachments

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    _upload(graph, blob, "a-old", org="org-1", conversation="conv-1", at=10)
    _upload(graph, blob, "b-old", org="org-1", conversation=None, at=10)
    _upload(graph, blob, "c-recent", org="org-1", conversation=None, at=500)
    _upload(graph, blob, "d-other-org", org="org-2", conversation=None, at=10)

    first = await authed(list_unsent_attachments)(_request({"before": 100, "limit": 1}), graph, {"orgId": "org-1"})
    second = await authed(list_unsent_attachments)(
        _request({"before": 100, "limit": 1, "after": first["next"]}), graph, {"orgId": "org-1"},
    )

    assert first == {
        "uploads": [{"recordId": "a-old", "ownerId": "u1", "conversationId": "conv-1"}], "remaining": True, "next": "a-old",
    }
    assert second["uploads"] == [{"recordId": "b-old", "ownerId": "u1", "conversationId": None}]
    assert len(graph.nodes["records"]) == 4, "listing erases nothing"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"before": 1},
        {"before": -1, "limit": 1},
        {"before": 1, "limit": 0},
        {"before": 1, "limit": 51},
        {"before": 1, "limit": 1, "after": ""},
        {"before": 1, "limit": 1, "orgId": "org-2"},
    ],
)
async def test_cleanup_g5_a_listing_names_a_time_and_one_to_fifty_uploads_and_never_the_org(body: dict[str, Any]) -> None:
    from app.api.routes.chatbot import list_unsent_attachments

    with pytest.raises(HTTPException) as refused:
        await authed(list_unsent_attachments)(_request(body), FakeGraphProvider(), {"orgId": "org-1"})
    assert refused.value.status_code == 400


async def test_cleanup_g5_the_collector_routes_refuse_a_token_without_an_org() -> None:
    from app.api.routes.chatbot import list_unsent_attachments, mark_attachments_sent

    for route, body in ((list_unsent_attachments, {"before": 1, "limit": 1}), (mark_attachments_sent, {"recordIds": ["a"]})):
        with pytest.raises(HTTPException) as refused:
            await route(_request(body), FakeGraphProvider(), {})
        assert refused.value.status_code == 400
