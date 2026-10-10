"""Calls for the PH-06 collaboration routes (chat kind by default, agent kind with ``agent_key``)."""

from __future__ import annotations

import itertools
from typing import Any

import requests

from helper.collab_stack.chats import CONVERSATIONS
from helper.collab_stack.client import Api
from helper.collab_stack.identity import Actor
from helper.collab_stack.stack import CollabStack

_counter = itertools.count(1)

Row = tuple[str, str, str]  # (principalType, principalId, accessLevel)


def base(conversation_id: str, agent_key: str | None = None) -> str:
    prefix = f"/api/v1/agents/{agent_key}/conversations" if agent_key else CONVERSATIONS
    return f"{prefix}/{conversation_id}"


def user(actor: Actor, level: str = "write") -> Row:
    return ("user", actor.user_id, level)


def team(team_id: str, level: str = "read") -> Row:
    return ("team", team_id, level)


def put(
    api: Api,
    who: Actor,
    conversation_id: str,
    *rows: Row,
    note: str | None = None,
    confirm_org_wide: bool = False,
    agent_key: str | None = None,
) -> requests.Response:
    body: dict[str, Any] = {"collaborators": [{"principalType": t, "principalId": i, "accessLevel": level} for t, i, level in rows]}
    if note is not None:
        body["note"] = note
    if confirm_org_wide:
        body["confirmOrgWide"] = True
    return api.put(f"{base(conversation_id, agent_key)}/collaborators", who, json_body=body)


def ok(resp: requests.Response, what: str = "request") -> dict[str, Any]:
    assert resp.status_code == 200, f"{what}: {resp.status_code} {resp.text[:400]}"
    return resp.json()


def list_(api: Api, who: Actor, conversation_id: str, agent_key: str | None = None) -> requests.Response:
    return api.get(f"{base(conversation_id, agent_key)}/collaborators", who)


def remove(
    api: Api, who: Actor, conversation_id: str, principal_id: str, principal_type: str = "user", agent_key: str | None = None
) -> requests.Response:
    return api.delete(f"{base(conversation_id, agent_key)}/collaborators/{principal_id}", who, params={"principalType": principal_type})


def settings(api: Api, who: Actor, conversation_id: str, agent_key: str | None = None, **patch: bool) -> requests.Response:
    return api.patch(f"{base(conversation_id, agent_key)}/collaboration-settings", who, json_body=patch)


def transfer(api: Api, who: Actor, conversation_id: str, new_owner: Actor, agent_key: str | None = None) -> requests.Response:
    return api.post(f"{base(conversation_id, agent_key)}/transfer-ownership", who, json_body={"newOwnerUserId": new_owner.user_id})


def leave(api: Api, who: Actor, conversation_id: str, agent_key: str | None = None) -> requests.Response:
    return api.post(f"{base(conversation_id, agent_key)}/leave", who)


def feed(api: Api, who: Actor, conversation_id: str, agent_key: str | None = None, **params: Any) -> requests.Response:
    return api.get(f"{base(conversation_id, agent_key)}/feed", who, params=params or None)


def readiness(api: Api, who: Actor, conversation_id: str, agent_key: str | None = None) -> requests.Response:
    return api.get(f"{base(conversation_id, agent_key)}/readiness", who)


def note(api: Api, who: Actor, conversation_id: str, *mentions: dict[str, str], query: str = "FYI", client_id: str = "n1", agent_key: str | None = None) -> requests.Response:
    """Post a note (a message to participants that the assistant does not answer)."""
    return api.post(f"{base(conversation_id, agent_key)}/notes", who, json_body={"query": query, "mentions": list(mentions), "clientMessageId": client_id})


def mentionables(api: Api, who: Actor, conversation_id: str, agent_key: str | None = None) -> list[dict[str, Any]]:
    """What the composer's `@` picker offers this caller in the chat."""
    resp = api.get(f"{base(conversation_id, agent_key)}/mentionables", who)
    assert resp.status_code == 200, f"mentionables: {resp.status_code} {resp.text[:300]}"
    return resp.json()["items"]


def fresh_actor(stack: CollabStack, label: str, **kw: Any) -> Actor:
    """A new user in org acme. Rate-limit budgets, the 60 s owner-status memory and agent-readiness caches are per
    user, so a journey that creates its own owner is isolated from the tests that ran before it."""
    assert stack.directory is not None
    actor = stack.directory.user(f"{label}{next(_counter)}x{stack.run_dir.name[-4:]}", **kw)
    stack.sync_graph_users(actor)
    return actor
