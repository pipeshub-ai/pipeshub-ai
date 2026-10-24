"""Which identity a service agent searches knowledge as.

A service agent is shared with the whole organisation, so the identity chosen
here is the identity every colleague's questions run under. These cover the
two functions that decide it: the one that validates a binding when an
administrator sets it, and the one that resolves it on every chat.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes.agent import (
    AgentNotFoundError,
    InvalidRequestError,
    _enrich_user_info_for_service_account_agent_chat,
    _resolve_service_account_binding,
)

ORG = "org-1"
OTHER_ORG = "org-2"

CREATOR = {
    "_key": "k-creator",
    "userId": "u-creator",
    "orgId": ORG,
    "email": "admin@acme.test",
    "kind": "human",
}
ACCOUNT = {
    "_key": "k-account",
    "userId": "u-account",
    "orgId": ORG,
    "email": "svc-nightly-org1@service.pipeshub.internal",
    "kind": "service",
}


class FakeGraph:
    """Only the two calls these functions make."""

    def __init__(self, by_key: dict[str, dict[str, Any]] | None = None,
                 by_user_id: dict[str, dict[str, Any]] | None = None) -> None:
        self._by_key = by_key or {}
        self._by_user_id = by_user_id or {}
        self.raised = False

    async def get_document(self, key: str, _collection: str) -> dict[str, Any] | None:
        return self._by_key.get(key)

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False):
        if self.raised:
            raise RuntimeError("graph unavailable")
        return self._by_user_id.get(user_id)


def agent(**overrides: Any) -> dict[str, Any]:
    base = {"_key": "a-1", "isServiceAccount": True, "createdBy": CREATOR["_key"]}
    base.update(overrides)
    return base


LOG = logging.getLogger("test")


# --- resolving a binding an administrator is setting -------------------------

@pytest.mark.asyncio
async def test_binding_resolves_a_service_account_to_its_node_key() -> None:
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})
    key = await _resolve_service_account_binding(ACCOUNT["userId"], graph, ORG)
    assert key == ACCOUNT["_key"]


@pytest.mark.asyncio
async def test_binding_refuses_a_colleague() -> None:
    """The point of binding is to stop one person's reach being lent out, so a
    human user id must not be accepted however it is spelled."""
    graph = FakeGraph(by_user_id={CREATOR["userId"]: CREATOR})
    with pytest.raises(InvalidRequestError):
        await _resolve_service_account_binding(CREATOR["userId"], graph, ORG)


@pytest.mark.asyncio
async def test_binding_refuses_a_user_with_no_kind_at_all() -> None:
    """A node written before `kind` existed is not evidence of anything."""
    unmarked = {k: v for k, v in ACCOUNT.items() if k != "kind"}
    graph = FakeGraph(by_user_id={unmarked["userId"]: unmarked})
    with pytest.raises(InvalidRequestError):
        await _resolve_service_account_binding(unmarked["userId"], graph, ORG)


@pytest.mark.asyncio
async def test_binding_refuses_another_organisations_service_account() -> None:
    foreign = {**ACCOUNT, "orgId": OTHER_ORG}
    graph = FakeGraph(by_user_id={foreign["userId"]: foreign})
    with pytest.raises(InvalidRequestError):
        await _resolve_service_account_binding(foreign["userId"], graph, ORG)


@pytest.mark.asyncio
async def test_binding_refuses_an_empty_choice() -> None:
    with pytest.raises(InvalidRequestError):
        await _resolve_service_account_binding("   ", FakeGraph(), ORG)


@pytest.mark.asyncio
async def test_binding_lets_a_database_failure_surface() -> None:
    """Not as "no such account" — that sends an administrator looking for a
    problem that is not there."""
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})
    graph.raised = True
    with pytest.raises(RuntimeError):
        await _resolve_service_account_binding(ACCOUNT["userId"], graph, ORG)


# --- resolving the identity on every chat ------------------------------------

@pytest.mark.asyncio
async def test_bound_agent_searches_as_the_service_account() -> None:
    graph = FakeGraph(by_key={CREATOR["_key"]: CREATOR, ACCOUNT["_key"]: ACCOUNT})
    info = await _enrich_user_info_for_service_account_agent_chat(
        agent(serviceAccountUserId=ACCOUNT["_key"]), graph, LOG, ORG,
    )
    assert info["userId"] == ACCOUNT["userId"]


@pytest.mark.asyncio
async def test_unbound_agent_still_searches_as_its_creator() -> None:
    """The older behaviour, kept deliberately: ending it here would cut off
    every service agent that works today."""
    graph = FakeGraph(by_key={CREATOR["_key"]: CREATOR})
    info = await _enrich_user_info_for_service_account_agent_chat(
        agent(), graph, LOG, ORG,
    )
    assert info["userId"] == CREATOR["userId"]


@pytest.mark.asyncio
async def test_a_binding_that_stopped_being_a_service_account_is_refused() -> None:
    """Not quietly used, and not fallen back from: falling back to the creator
    would widen the agent's reach exactly when the narrow identity broke."""
    demoted = {**ACCOUNT, "kind": "human"}
    graph = FakeGraph(by_key={CREATOR["_key"]: CREATOR, demoted["_key"]: demoted})
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=demoted["_key"]), graph, LOG, ORG,
        )
    assert caught.value.status_code == 500


@pytest.mark.asyncio
async def test_a_binding_in_another_org_is_refused() -> None:
    """Checked again on every run: an account can be moved after it was bound."""
    foreign = {**ACCOUNT, "orgId": OTHER_ORG}
    graph = FakeGraph(by_key={CREATOR["_key"]: CREATOR, foreign["_key"]: foreign})
    with pytest.raises(AgentNotFoundError):
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=foreign["_key"]), graph, LOG, ORG,
        )


@pytest.mark.asyncio
async def test_a_binding_that_no_longer_exists_is_refused() -> None:
    graph = FakeGraph(by_key={CREATOR["_key"]: CREATOR})
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId="k-gone"), graph, LOG, ORG,
        )
    assert caught.value.status_code == 500


@pytest.mark.asyncio
async def test_unbound_agent_in_another_org_is_still_refused() -> None:
    foreign_creator = {**CREATOR, "orgId": OTHER_ORG}
    graph = FakeGraph(by_key={foreign_creator["_key"]: foreign_creator})
    with pytest.raises(AgentNotFoundError):
        await _enrich_user_info_for_service_account_agent_chat(
            agent(), graph, LOG, ORG,
        )
