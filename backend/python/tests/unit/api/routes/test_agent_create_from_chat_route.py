"""Who may set an agent's chat provenance (AB-02, PH11-05 server side) and the handle check."""

from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.support.agent_routes import AGENTS, InMemoryGraph, as_user, make_client

BASE = "/api/v1/agent"

BOUND = {
    "token_type": "scoped", "userId": "u-alice", "orgId": "org-1", "email": "alice@acme.test",
    "scopes": ["agent:create:chat"], "conversationId": "c-1", "messageId": "m-1",
}
USERS: dict[str, dict[str, Any]] = {
    "node": BOUND,
    "node-unbound": {k: v for k, v in BOUND.items() if k != "messageId"},
    "node-cancel-token": {**BOUND, "scopes": ["conversation:cancel"]},
}


def _post(client: TestClient, user: str, **body: object) -> Response:
    return client.post(f"{BASE}/internal/create-from-chat", headers=as_user(user), json={"name": "Offer drafter", **body})


def test_a_user_token_cannot_use_the_chat_route() -> None:
    graph = InMemoryGraph()
    client, _ = make_client(graph, extra_users=USERS)

    response = _post(client, "alice", createdVia="chat", sourceConversationId="c-x", sourceMessageId="m-x")

    assert response.status_code == 403
    assert graph.nodes.get(AGENTS, {}) == {}


def test_a_service_token_with_another_scope_is_refused() -> None:
    client, _ = make_client(InMemoryGraph(), extra_users=USERS)

    assert _post(client, "node-cancel-token").status_code == 403


def test_a_token_not_bound_to_a_draft_is_refused() -> None:
    client, _ = make_client(InMemoryGraph(), extra_users=USERS)

    assert _post(client, "node-unbound").status_code == 403


def test_provenance_comes_from_the_token_not_the_body() -> None:
    graph = InMemoryGraph()
    client, _ = make_client(graph, extra_users=USERS)

    response = _post(client, "node", createdVia="ui", sourceConversationId="c-forged", sourceMessageId="m-forged", shareWithOrg=True)

    assert response.status_code == 200
    stored = graph.nodes[AGENTS][response.json()["agent"]["_key"]]
    assert (stored["createdVia"], stored["sourceConversationId"], stored["sourceMessageId"]) == ("chat", "c-1", "m-1")
    assert stored["createdBy"] == "k-alice" and stored["orgId"] == "org-1"
    assert {e["type"] for e in graph.edges["permission"]} == {"USER"}


def test_the_ui_route_ignores_provenance_in_the_body() -> None:
    graph = InMemoryGraph()
    client, _ = make_client(graph, extra_users=USERS)

    response = client.post(
        f"{BASE}/create", headers=as_user("alice"),
        json={"name": "X", "createdVia": "chat", "sourceConversationId": "c-1", "sourceMessageId": "m-1"},
    )

    stored = graph.nodes[AGENTS][response.json()["agent"]["_key"]]
    assert stored["createdVia"] == "ui" and "sourceConversationId" not in stored and "sourceMessageId" not in stored


def test_the_chat_route_relays_the_access_error_body() -> None:
    client, _ = make_client(InMemoryGraph(), extra_users=USERS)

    response = _post(client, "node", isServiceAccount=True)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "SERVICE_ACCOUNT_NOT_ALLOWED"


class TestHandleAvailability:
    def _check(self, client, handle: str, user: str = "alice") -> dict[str, Any]:
        response = client.get(f"{BASE}/handle-availability", headers=as_user(user), params={"handle": handle})
        assert response.status_code == 200
        return response.json()

    def test_free_handle(self) -> None:
        client, _ = make_client(InMemoryGraph())

        assert self._check(client, "@offer-drafter") == {"available": True}

    def test_taken_handle_suggests_the_next_free_one_even_for_an_agent_the_caller_cannot_see(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("bobs", "bob", orgId="org-1", handle="offer-drafter")
        client, _ = make_client(graph)

        assert self._check(client, "offer-drafter") == {"available": False, "reason": "taken", "suggestion": "offer-drafter-2"}

    def test_other_orgs_handles_do_not_count(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("evil", "mallory", orgId="org-2", handle="offer-drafter")
        client, _ = make_client(graph)

        assert self._check(client, "offer-drafter") == {"available": True}

    def test_malformed_and_reserved(self) -> None:
        client, _ = make_client(InMemoryGraph())

        assert self._check(client, "Bad Handle")["reason"] == "invalid"
        assert self._check(client, "assistant")["reason"] == "reserved"

    def test_requires_a_user(self) -> None:
        client, _ = make_client(InMemoryGraph())

        assert client.get(f"{BASE}/handle-availability", params={"handle": "x1"}).status_code == 401
