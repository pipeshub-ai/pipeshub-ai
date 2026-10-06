"""Chat and project calls shared by the journeys (thin wrappers; assertions stay in the tests)."""

from __future__ import annotations

from typing import Any

import requests

from helper.collab_stack.client import Api
from helper.collab_stack.identity import Actor

CONVERSATIONS = "/api/v1/conversations"
AGENT_KEY = "agent-1"


def create_chat(api: Api, who: Actor, query: str = "hello", **body: Any) -> str:
    """Start a chat through the public API (non-streaming) and return its id."""
    resp = api.post(f"{CONVERSATIONS}/create", who, json_body={"query": query, "chatMode": "quick", **body})
    assert resp.status_code == 201, f"create chat: {resp.status_code} {resp.text[:300]}"
    return resp.json()["conversation"]["_id"]


def share(api: Api, owner: Actor, conversation_id: str, *recipients: Actor, level: str = "read") -> requests.Response:
    return api.post(
        f"{CONVERSATIONS}/{conversation_id}/share",
        owner,
        json_body={"userIds": [r.user_id for r in recipients], "accessLevel": level},
    )


def share_as_writer(api: Api, owner: Actor, conversation_id: str, *recipients: Actor) -> None:
    """Share at ``write`` through the public route, which honours ``accessLevel`` while collaborative chats are on."""
    resp = share(api, owner, conversation_id, *recipients, level="write")
    assert resp.status_code == 200, f"share: {resp.status_code} {resp.text[:300]}"
    assert "warnings" not in resp.json(), f"write was downgraded: {resp.json()['warnings']}"


def unshare(api: Api, owner: Actor, conversation_id: str, *recipients: Actor) -> requests.Response:
    return api.post(f"{CONVERSATIONS}/{conversation_id}/unshare", owner, json_body={"userIds": [r.user_id for r in recipients]})


def get_chat(api: Api, who: Actor, conversation_id: str, **params: Any) -> requests.Response:
    return api.get(f"{CONVERSATIONS}/{conversation_id}", who, params=params or None)


def send_message(api: Api, who: Actor, conversation_id: str, query: str = "more", **body: Any) -> requests.Response:
    return api.post(f"{CONVERSATIONS}/{conversation_id}/messages", who, json_body={"query": query, "chatMode": "quick", **body})


def regenerate(api: Api, who: Actor, conversation_id: str, message_id: str, *, agent_key: str | None = None) -> requests.Response:
    base = f"/api/v1/agents/{agent_key}/conversations" if agent_key else CONVERSATIONS
    return api.post(f"{base}/{conversation_id}/message/{message_id}/regenerate", who, json_body={"chatMode": "quick"})


def create_project(api: Api, owner: Actor, name: str = "project") -> str:
    resp = api.post("/api/v1/projects", owner, json_body={"name": name})
    assert resp.status_code == 201, f"create project: {resp.status_code} {resp.text[:300]}"
    return resp.json()["project"]["_id"]


def add_project_member(
    api: Api, owner: Actor, project_id: str, principal_id: str, role: str = "viewer", principal_type: str = "user"
) -> requests.Response:
    return api.put(
        f"/api/v1/projects/{project_id}/members",
        owner,
        json_body={"members": [{"principalId": principal_id, "principalType": principal_type, "role": role}]},
    )


def project_ids(resp: requests.Response) -> list[str]:
    assert resp.status_code == 200, resp.text[:300]
    return [p["_id"] for p in resp.json()["projects"]]


def error_of(resp: requests.Response) -> tuple[int, str | None]:
    """(status, error code) of a response; the code is None when the body is not the API's error shape."""
    try:
        return resp.status_code, resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return resp.status_code, None


def details_of(resp: requests.Response) -> dict[str, Any]:
    return resp.json()["error"].get("details", {})


def stream_path(conversation_id: str, agent_key: str | None = None) -> str:
    base = f"/api/v1/agents/{agent_key}/conversations" if agent_key else CONVERSATIONS
    return f"{base}/{conversation_id}/messages/stream"


def stream_message(api: Api, who: Actor, conversation_id: str, query: str = "more", **body: Any):  # noqa: ANN201
    """A streamed follow-up (C3); read it on a thread through the returned ``StreamCall``."""
    return api.stream(stream_path(conversation_id), who, json_body={"query": query, "chatMode": "internal_search", **body})


def cancel(api: Api, who: Actor, conversation_id: str, run_id: str) -> requests.Response:
    return api.post(f"{CONVERSATIONS}/{conversation_id}/cancel", who, json_body={"runId": run_id})
