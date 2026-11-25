"""H5 AE-15: whether an agent's delete committed, for Node's agent-erasure sweeper only."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.middlewares.auth import require_service_token
from app.api.routes.agent import get_agent_deletion_internal
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import TokenScopes
from app.harness.agent_deletion import AgentDeletionState, deletion_state
from tests.support.bound_org import authed

AGENTS = CollectionNames.AGENT_INSTANCES.value
USERS = CollectionNames.USERS.value


class _Graph:
    """``get_document`` as both providers answer it: documents unfiltered (tombstones included), ``None`` if absent."""

    def __init__(self, documents: dict[tuple[str, str], dict[str, Any]], *, failing: bool = False) -> None:
        self.documents = documents
        self.failing = failing
        self.calls: list[tuple[str, str, bool]] = []

    async def get_document(
        self, document_key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False
    ) -> dict[str, Any] | None:
        self.calls.append((document_key, collection, raise_on_error))
        if self.failing:
            raise ConnectionError("graph down")
        return self.documents.get((collection, document_key))


def _graph(*, deleted: bool | None, creator_org: str = "org-1") -> _Graph:
    agent: dict[str, Any] = {"_key": "agent-x", "createdBy": "u-1"}
    if deleted is not None:
        agent["isDeleted"] = deleted
    return _Graph({(AGENTS, "agent-x"): agent, (USERS, "u-1"): {"_key": "u-1", "orgId": creator_org}})


@pytest.mark.parametrize(
    ("graph", "org", "state"),
    [
        (_graph(deleted=True), "org-1", "deleted"),
        (_graph(deleted=False), "org-1", "live"),
        (_graph(deleted=None), "org-1", "live"),
        (_graph(deleted=True, creator_org="org-2"), "org-1", "absent"),
        (_Graph({}), "org-1", "absent"),
        (_Graph({(AGENTS, "agent-x"): {"_key": "agent-x", "isDeleted": True}}), "org-1", "absent"),
    ],
    ids=["tombstone", "live", "live-without-flag", "other-org", "unknown-key", "no-creator"],
)
async def test_ae15_the_state_is_read_with_tombstones_and_scoped_to_the_tokens_org(
    graph: _Graph, org: str, state: str
) -> None:
    assert await authed(get_agent_deletion_internal)("agent-x", graph, {"orgId": org}) == {"state": state}  # type: ignore[arg-type]
    assert all(raise_on_error for _key, _collection, raise_on_error in graph.calls)


async def test_ae15_a_graph_failure_is_a_503_never_an_answer() -> None:
    with pytest.raises(HTTPException) as failed:
        await authed(get_agent_deletion_internal)("agent-x", _Graph({}, failing=True), {"orgId": "org-1"})  # type: ignore[arg-type]
    assert failed.value.status_code == 503


async def test_ae15_a_token_without_an_org_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        await authed(get_agent_deletion_internal)("agent-x", _graph(deleted=True), {})  # type: ignore[arg-type]
    assert refused.value.status_code == 400


def _token_request(user: dict[str, Any] | None) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


@pytest.mark.parametrize(
    "user",
    [
        None,
        {"token_type": "regular", "orgId": "org-1", "userId": "u1"},
        {"token_type": "scoped", "orgId": "org-1", "scopes": ["conversation:erase"]},
        {"token_type": "scoped", "orgId": "org-1", "scopes": ["storage:token"]},
    ],
)
async def test_ae15_only_a_service_token_scoped_agent_erase_passes(user: dict[str, Any] | None) -> None:
    check = require_service_token(TokenScopes.AGENT_ERASE)
    with pytest.raises(HTTPException) as refused:
        await check(_token_request(user))
    assert refused.value.status_code == (401 if user is None else 403)
    granted = {"token_type": "scoped", "orgId": "org-1", "scopes": ["agent:erase"]}
    assert await check(_token_request(granted)) == granted


def test_ae15_only_a_tombstone_reads_as_deleted() -> None:
    creator = {"orgId": "org-1"}
    assert deletion_state({"isDeleted": True}, creator, "org-1") is AgentDeletionState.DELETED
    assert deletion_state({"isDeleted": "true"}, creator, "org-1") is AgentDeletionState.LIVE
    assert deletion_state({"isDeleted": True}, creator, "") is AgentDeletionState.ABSENT
    assert deletion_state({"isDeleted": True}, {"orgId": None}, "org-1") is AgentDeletionState.ABSENT
