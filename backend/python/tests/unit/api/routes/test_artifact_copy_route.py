"""SS3 (AC-5): the query service's artifact copy route, for Node's "continue privately" only."""

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
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

CLAIMS = {"orgId": "org-1", "userId": "bob"}


def _request(body: object) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = MagicMock()
    return request


def _token_request(user: dict[str, Any] | None) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


@pytest.mark.parametrize(
    "user",
    [
        None,
        {"token_type": "regular", "orgId": "org-1", "userId": "bob"},
        {"token_type": "scoped", "orgId": "org-1", "userId": "bob", "scopes": ["conversation:erase"]},
        {"token_type": "scoped", "orgId": "org-1", "userId": "bob", "scopes": ["conversation:permissions"]},
    ],
)
async def test_only_a_service_token_scoped_conversation_fork_passes(user: dict[str, Any] | None) -> None:
    check = require_service_token(TokenScopes.CONVERSATION_FORK)
    with pytest.raises(HTTPException) as refused:
        await check(_token_request(user))
    assert refused.value.status_code == (401 if user is None else 403)


async def test_copies_as_the_tokens_user_in_the_tokens_org() -> None:
    from app.api.routes.chatbot import copy_artifacts

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    graph.add_user("bob", key="ukey-bob")
    made = await ArtifactRegistryService(graph, blob).register(
        actor=Actor(org_id="org-1", user_id="bob"),
        name="b.txt",
        artifact_type=ArtifactType.DOCUMENT,
        mime_type="text/plain",
        content=b"bytes",
        conversation_id="conv-a",
    )

    async def fetch(*, storage_document_id: str, **_: object) -> bytes:
        return blob.documents[storage_document_id]["content"]

    body = {"fromConversationId": "conv-a", "toConversationId": "conv-b", "artifacts": [{"artifactId": made.artifact_id, "version": 1}, {"artifactId": "nope"}]}
    with patch("app.api.routes.chatbot.BlobStorage", return_value=blob), patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=fetch):
        out = await copy_artifacts(_request(body), graph, MagicMock(), CLAIMS)
    first, second = out["copies"]
    assert first["artifactId"] == made.artifact_id and first["version"] == 1
    assert first["copy"]["version"] == 1 and first["copy"]["artifactId"] != made.artifact_id
    assert "reason" not in first
    assert second == {"artifactId": "nope", "version": None, "copy": None, "reason": "not_found"}
    assert graph.nodes["artifacts"][first["copy"]["artifactId"]]["conversationId"] == "conv-b"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"fromConversationId": "a", "toConversationId": "b", "artifacts": []},
        {"fromConversationId": "a", "toConversationId": "a", "artifacts": [{"artifactId": "x"}]},
        {"fromConversationId": "a", "toConversationId": "b", "artifacts": [{"artifactId": "x", "version": 0}]},
        {"fromConversationId": "a", "toConversationId": "b", "artifacts": [{"artifactId": "x"}], "orgId": "org-2"},
        {"fromConversationId": "a", "toConversationId": "b", "artifacts": [{"artifactId": "x"}], "userId": "alice"},
        {"fromConversationId": "a", "toConversationId": "b", "artifacts": [{"artifactId": f"x{i}"} for i in range(201)]},
    ],
)
async def test_refuses_a_body_that_names_the_org_the_user_or_too_much(body: dict[str, Any]) -> None:
    from app.api.routes.chatbot import copy_artifacts

    with pytest.raises(HTTPException) as refused:
        await copy_artifacts(_request(body), FakeGraphProvider(), MagicMock(), CLAIMS)
    assert refused.value.status_code == 400


@pytest.mark.parametrize("claims", [{}, {"orgId": "org-1"}, {"userId": "bob"}])
async def test_refuses_a_token_without_org_and_user(claims: dict[str, Any]) -> None:
    from app.api.routes.chatbot import copy_artifacts

    body = {"fromConversationId": "a", "toConversationId": "b", "artifacts": [{"artifactId": "x"}]}
    with pytest.raises(HTTPException) as refused:
        await copy_artifacts(_request(body), FakeGraphProvider(), MagicMock(), claims)
    assert refused.value.status_code == 400
