from httpx import Response

from tests.support.agent_routes import AGENTS, InMemoryGraph, as_user, make_client

BASE = "/api/v1/agent"


def _create(client, name: str, user: str = "alice", **extra) -> Response:
    return client.post(f"{BASE}/create", headers=as_user(user), json={"name": name, **extra})


def test_create_returns_the_handle_on_the_agent() -> None:
    client, _ = make_client(InMemoryGraph())

    response = _create(client, "Offer drafter")

    assert response.status_code == 200
    assert response.json()["agent"]["handle"] == "offer-drafter"


def test_client_cannot_set_org_or_origin() -> None:
    graph = InMemoryGraph()
    client, _ = make_client(graph)

    body = _create(client, "Sneaky", orgId="org-2", createdVia="chat", sourceConversationId="c-1").json()

    stored = graph.nodes[AGENTS][body["agent"]["_key"]]
    assert (stored["orgId"], stored["createdVia"]) == ("org-1", "ui")
    assert "sourceConversationId" not in stored


def test_taken_handle_is_a_409_with_code_and_suggestion() -> None:
    client, _ = make_client(InMemoryGraph())
    _create(client, "Sales Bot")

    response = _create(client, "Other", handle="sales-bot")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "HANDLE_TAKEN", "message": "The handle @sales-bot is already taken.", "suggestion": "sales-bot-2",
    }


def test_reserved_handle_is_a_400() -> None:
    client, _ = make_client(InMemoryGraph())

    response = _create(client, "Other", handle="assistant")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "HANDLE_RESERVED"


def test_get_and_list_include_the_handle() -> None:
    client, _ = make_client(InMemoryGraph())
    key = _create(client, "Sales Bot").json()["agent"]["_key"]

    single = client.get(f"{BASE}/{key}", headers=as_user("alice")).json()["agent"]
    listed = client.get(f"{BASE}/", headers=as_user("alice")).json()["agents"]

    assert single["handle"] == "sales-bot"
    assert [a["handle"] for a in listed] == ["sales-bot"]


def test_put_changes_the_handle_and_reports_conflicts() -> None:
    client, _ = make_client(InMemoryGraph())
    first = _create(client, "First").json()["agent"]["_key"]
    _create(client, "Second")

    ok = client.put(f"{BASE}/{first}", headers=as_user("alice"), json={"handle": "renamed"})
    clash = client.put(f"{BASE}/{first}", headers=as_user("alice"), json={"handle": "second"})

    assert ok.status_code == 200
    assert clash.status_code == 409 and clash.json()["detail"]["suggestion"] == "second-2"
    assert client.get(f"{BASE}/{first}", headers=as_user("alice")).json()["agent"]["handle"] == "renamed"
