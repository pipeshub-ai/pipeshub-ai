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

from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes.agent import (
    AgentNotFoundError,
    InvalidRequestError,
    _checked_service_account_binding,
    _enrich_user_info_for_service_account_agent_chat,
    _only_an_administrator_may_bind,
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


class Caller:
    """Stands in for the live role lookup, and counts whether it was consulted.

    The real one asks Node what role the request's own credentials carry; here
    the answer is decided up front so a test can say who is asking.
    """

    def __init__(self, *, admin: bool) -> None:
        self.admin = admin
        self.asked = 0

    async def __call__(self) -> None:
        self.asked += 1
        if not self.admin:
            raise HTTPException(status_code=403, detail="not an administrator")


class FakeGraph:
    """Only the two calls these functions make."""

    def __init__(self, by_key: dict[str, dict[str, Any]] | None = None,
                 by_user_id: dict[str, dict[str, Any]] | None = None) -> None:
        self._by_key = by_key or {}
        self._by_user_id = by_user_id or {}
        self.raised = False

    async def get_document(self, key: str, _collection: str) -> dict[str, Any] | None:
        return self._by_key.get(key)

    async def get_user_by_user_id(
        self, user_id: str, *, raise_on_error: bool = False,
    ) -> dict[str, Any] | None:
        if self.raised:
            raise RuntimeError("graph unavailable")
        return self._by_user_id.get(user_id)


def agent(**overrides: object) -> dict[str, Any]:
    base = {"_key": "a-1", "isServiceAccount": True, "createdBy": CREATOR["_key"]}
    base.update(overrides)
    return base


LOG = logging.getLogger("test")


# --- resolving a binding an administrator is setting -------------------------

@pytest.mark.asyncio
async def test_binding_gives_back_the_same_user_id_it_was_given() -> None:
    """What a picker sends is what gets stored, so what it reads back matches
    the list it chose from. The node's own key would match nothing there."""
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})
    bound = await _resolve_service_account_binding(ACCOUNT["userId"], graph, ORG)
    assert bound == ACCOUNT["userId"]
    assert bound != ACCOUNT["_key"]


@pytest.mark.asyncio
async def test_binding_refuses_a_deleted_service_account() -> None:
    """Soft-delete leaves the node and only clears isActive, so kind alone
    would still accept it."""
    deleted = {**ACCOUNT, "isActive": False}
    graph = FakeGraph(by_user_id={deleted["userId"]: deleted})
    with pytest.raises(InvalidRequestError):
        await _resolve_service_account_binding(deleted["userId"], graph, ORG)


@pytest.mark.asyncio
async def test_binding_refuses_a_disabled_service_account() -> None:
    """Switched off is its own field, because a rename sets isActive back on."""
    off = {**ACCOUNT, "isDisabled": True}
    graph = FakeGraph(by_user_id={off["userId"]: off})
    with pytest.raises(InvalidRequestError):
        await _resolve_service_account_binding(off["userId"], graph, ORG)


@pytest.mark.asyncio
async def test_binding_accepts_an_account_that_was_switched_back_on() -> None:
    back_on = {**ACCOUNT, "isDisabled": False}
    graph = FakeGraph(by_user_id={back_on["userId"]: back_on})
    assert (
        await _resolve_service_account_binding(back_on["userId"], graph, ORG)
        == back_on["userId"]
    )


@pytest.mark.asyncio
async def test_binding_accepts_a_node_that_predates_is_active() -> None:
    """Absence is read as live; treating it as deleted would refuse accounts
    that work today."""
    older = dict(ACCOUNT)
    graph = FakeGraph(by_user_id={older["userId"]: older})
    assert await _resolve_service_account_binding(older["userId"], graph, ORG) == older["userId"]


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
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={ACCOUNT["userId"]: ACCOUNT},
    )
    info = await _enrich_user_info_for_service_account_agent_chat(
        agent(serviceAccountUserId=ACCOUNT["userId"]), graph, LOG, ORG,
    )
    assert info["userId"] == ACCOUNT["userId"]


@pytest.mark.asyncio
async def test_a_binding_stored_as_a_user_id_is_looked_up_as_one() -> None:
    """The creator is a node key and a binding is a userId: two identifiers,
    so two different lookups. Reading a binding by key finds nothing."""
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR, ACCOUNT["_key"]: ACCOUNT}, by_user_id={},
    )
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=ACCOUNT["userId"]), graph, LOG, ORG,
        )
    assert caught.value.status_code == 500


@pytest.mark.asyncio
async def test_a_binding_to_a_deleted_account_is_refused_on_every_chat() -> None:
    """Checked on each run, not only when it was set: an account can be
    deleted long after an administrator bound it."""
    deleted = {**ACCOUNT, "isActive": False}
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={deleted["userId"]: deleted},
    )
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=deleted["userId"]), graph, LOG, ORG,
        )
    assert caught.value.status_code == 500


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
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={demoted["userId"]: demoted},
    )
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=demoted["userId"]), graph, LOG, ORG,
        )
    assert caught.value.status_code == 500


@pytest.mark.asyncio
async def test_a_binding_in_another_org_is_refused() -> None:
    """Checked again on every run: an account can be moved after it was bound."""
    foreign = {**ACCOUNT, "orgId": OTHER_ORG}
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={foreign["userId"]: foreign},
    )
    with pytest.raises(AgentNotFoundError):
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=foreign["userId"]), graph, LOG, ORG,
        )


@pytest.mark.asyncio
async def test_a_binding_that_no_longer_exists_is_refused() -> None:
    graph = FakeGraph(by_key={CREATOR["_key"]: CREATOR})
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId="u-gone"), graph, LOG, ORG,
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


@pytest.mark.asyncio
async def test_a_binding_to_a_disabled_account_is_refused_on_every_chat() -> None:
    """An account can be switched off long after an administrator bound it,
    and an agent is shared org-wide, so this is checked on every run."""
    off = {**ACCOUNT, "isDisabled": True}
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={off["userId"]: off},
    )
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=off["userId"]), graph, LOG, ORG,
        )
    assert caught.value.status_code == 500
    # Not "deleted": switching it back on makes this agent work again, which
    # is a different thing for an administrator to do.
    assert "switched off" in caught.value.detail
    assert "deleted" not in caught.value.detail


@pytest.mark.asyncio
async def test_a_deleted_binding_still_says_deleted() -> None:
    gone = {**ACCOUNT, "isActive": False}
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={gone["userId"]: gone},
    )
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=gone["userId"]), graph, LOG, ORG,
        )
    assert "deleted" in caught.value.detail


@pytest.mark.asyncio
async def test_an_account_switched_off_and_then_deleted_says_deleted() -> None:
    """Soft-delete leaves isDisabled as it found it, so both flags are set.
    Telling an administrator to switch it back on would send them after
    something that cannot work — it is gone from the list as well."""
    both = {**ACCOUNT, "isDisabled": True, "isActive": False}
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={both["userId"]: both},
    )
    with pytest.raises(HTTPException) as caught:
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=both["userId"]), graph, LOG, ORG,
        )
    assert "deleted" in caught.value.detail
    assert "switched off" not in caught.value.detail


@pytest.mark.asyncio
async def test_a_disabled_binding_does_not_fall_back_to_the_creator() -> None:
    """Falling back would widen the agent's reach at the very moment the
    narrower identity stopped being allowed."""
    off = {**ACCOUNT, "isDisabled": True}
    graph = FakeGraph(
        by_key={CREATOR["_key"]: CREATOR}, by_user_id={off["userId"]: off},
    )
    with pytest.raises(HTTPException):
        await _enrich_user_info_for_service_account_agent_chat(
            agent(serviceAccountUserId=off["userId"]), graph, LOG, ORG,
        )


@pytest.mark.asyncio
async def test_an_unbound_agent_is_unaffected_by_the_disabled_field() -> None:
    """A person never carries it, and an agent that searches as its creator
    must not start failing because of a field that is not about them."""
    graph = FakeGraph(by_key={CREATOR["_key"]: CREATOR})
    info = await _enrich_user_info_for_service_account_agent_chat(
        agent(), graph, LOG, ORG,
    )
    assert info["userId"] == CREATOR["userId"]


# ---------------------------------------------------------------------------
# What an agent is allowed to ask for, before the account itself is looked up.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_binding_is_allowed_on_an_agent_that_is_not_a_service_agent() -> None:
    """Every agent document carries this field as null, so a client that reads
    an agent and sends it straight back must not be turned away."""
    graph = FakeGraph()
    graph.raised = True  # the account must not be looked up at all

    assert (
        await _checked_service_account_binding(
            None, graph, ORG, is_service_account=False, admin_check=Caller(admin=True),
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_binding_is_allowed_on_a_service_agent_too() -> None:
    """Clearing returns the agent to searching as its creator, which is where
    every unbound service agent already sits."""
    graph = FakeGraph()
    graph.raised = True

    assert (
        await _checked_service_account_binding(
            None, graph, ORG, is_service_account=True, admin_check=Caller(admin=True),
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_binding_on_a_regular_agent_is_refused_not_dropped() -> None:
    """Dropping it would leave the agent searching as its creator — the wider
    identity — while whoever asked believed they had narrowed it."""
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})

    with pytest.raises(InvalidRequestError):
        await _checked_service_account_binding(
            ACCOUNT["userId"], graph, ORG, is_service_account=False,
            admin_check=Caller(admin=True),
        )


@pytest.mark.asyncio
async def test_a_binding_on_a_service_agent_is_resolved() -> None:
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})

    assert (
        await _checked_service_account_binding(
            ACCOUNT["userId"], graph, ORG, is_service_account=True,
            admin_check=Caller(admin=True),
        )
        == ACCOUNT["userId"]
    )


# ---------------------------------------------------------------------------
# Who is allowed to choose the account. The binding spends what an
# administrator granted the service account, usually through group
# membership, so setting one is an administrator's decision even though
# creating and editing an agent is not.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_member_cannot_bind_an_agent_to_a_service_account() -> None:
    """The whole finding: creating an agent needs only a session, so without
    this any colleague could point one at an account an administrator put in a
    sensitive group and ask questions with its reach."""
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})
    graph.raised = True  # the account must not be looked up for a member
    caller = Caller(admin=False)

    with pytest.raises(HTTPException) as refused:
        await _checked_service_account_binding(
            ACCOUNT["userId"], graph, ORG,
            is_service_account=True, admin_check=caller,
        )

    assert refused.value.status_code == 403
    assert caller.asked == 1


@pytest.mark.asyncio
async def test_an_administrator_can_still_bind() -> None:
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})
    caller = Caller(admin=True)

    bound = await _checked_service_account_binding(
        ACCOUNT["userId"], graph, ORG,
        is_service_account=True, admin_check=caller,
    )

    assert bound == ACCOUNT["userId"]
    assert caller.asked == 1


@pytest.mark.asyncio
async def test_re_saving_the_same_binding_asks_no_one() -> None:
    """A client that reads an agent and saves it back sends this field every
    time, so an owner renaming a bound agent must not be turned away."""
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})
    caller = Caller(admin=False)

    bound = await _checked_service_account_binding(
        ACCOUNT["userId"], graph, ORG,
        is_service_account=True, admin_check=caller,
        current=ACCOUNT["userId"],
    )

    assert bound == ACCOUNT["userId"]
    assert caller.asked == 0


@pytest.mark.asyncio
async def test_leaving_an_unbound_agent_unbound_asks_no_one() -> None:
    graph = FakeGraph()
    graph.raised = True
    caller = Caller(admin=False)

    assert (
        await _checked_service_account_binding(
            None, graph, ORG, is_service_account=True, admin_check=caller,
        )
        is None
    )
    assert caller.asked == 0


@pytest.mark.asyncio
async def test_a_member_cannot_clear_an_administrators_binding() -> None:
    """Clearing is a change too: it returns the agent to searching as its
    creator, the wider identity the binding was set to get away from."""
    graph = FakeGraph(by_user_id={ACCOUNT["userId"]: ACCOUNT})
    caller = Caller(admin=False)

    with pytest.raises(HTTPException) as refused:
        await _checked_service_account_binding(
            None, graph, ORG,
            is_service_account=True, admin_check=caller,
            current=ACCOUNT["userId"],
        )

    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_a_member_is_refused_before_the_account_is_looked_up() -> None:
    """A refusal must not depend on whether the id exists, or it would say so."""
    empty = FakeGraph()  # nothing to find
    caller = Caller(admin=False)

    with pytest.raises(HTTPException) as refused:
        await _checked_service_account_binding(
            "u-does-not-exist", empty, ORG,
            is_service_account=True, admin_check=caller,
        )

    assert refused.value.status_code == 403


# --- the role lookup behind the gate ---------------------------------------


class FakeRequest:
    headers: dict[str, str] = {}


@pytest.mark.asyncio
async def test_an_administrator_passes_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def valid_admin(_request: object, _config: object) -> CallerRole:
        return CallerRole(CallerRoleStatus.VALID, "admin")

    monkeypatch.setattr("app.api.routes.agent.fetch_caller_role", valid_admin)
    await _only_an_administrator_may_bind(FakeRequest(), None)


@pytest.mark.asyncio
async def test_a_member_is_refused_by_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def valid_member(_request: object, _config: object) -> CallerRole:
        return CallerRole(CallerRoleStatus.VALID, "member")

    monkeypatch.setattr("app.api.routes.agent.fetch_caller_role", valid_member)

    with pytest.raises(HTTPException) as refused:
        await _only_an_administrator_may_bind(FakeRequest(), None)
    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_an_unconfirmed_role_is_not_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """503, not 403. "You are not an administrator" is the wrong thing to tell
    an administrator whose save failed because the lookup timed out — and it
    still does not let the change through."""
    async def unknown(_request: object, _config: object) -> CallerRole:
        return CallerRole(CallerRoleStatus.UNKNOWN)

    monkeypatch.setattr("app.api.routes.agent.fetch_caller_role", unknown)

    with pytest.raises(HTTPException) as refused:
        await _only_an_administrator_may_bind(FakeRequest(), None)
    assert refused.value.status_code == 503
