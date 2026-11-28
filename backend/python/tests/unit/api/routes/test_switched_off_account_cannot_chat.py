"""A removed, switched-off or missing account cannot use the assistant.

The auth layer may still accept a session for a short while after the account
is switched off, so every route that admits an assistant run reads the
caller's own record first and refuses unless it is a live person. A failed
read is an outage (503), never a live person.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middlewares.identified_caller import UNIDENTIFIED_CALLER_DETAIL
from app.api.routes.chatbot import router as chatbot_router
from tests.support.agent_routes import (
    USERS,
    FakeConfigService,
    FakeContainer,
    InMemoryGraph,
    _TestAuth,
    as_user,
    make_client,
    user_key,
)

ASSISTANT = "agentIdPlaceholder"


def _switch_off(graph: InMemoryGraph, state: str) -> None:
    node = graph.nodes["users"][user_key("bob")]
    if state == "removed":
        node["isActive"] = False
    elif state == "disabled":
        node["isDisabled"] = True
    elif state == "missing":
        del graph.nodes["users"][user_key("bob")]
    elif state == "service":
        node["kind"] = "service"


def _chatbot_client(graph: InMemoryGraph) -> TestClient:
    app = FastAPI()
    app.include_router(chatbot_router, prefix="/api/v1")
    app.container = FakeContainer(graph, FakeConfigService())
    app.add_middleware(_TestAuth, users=USERS)
    return TestClient(app, raise_server_exceptions=False)


def _assistant_chat(graph: InMemoryGraph, path: str) -> Any:  # noqa: ANN401
    client = make_client(graph)[0]
    return client.post(f"/api/v1/agent/{ASSISTANT}/{path}", headers=as_user("bob"), json={"query": "hi"})


def _run_definition(graph: InMemoryGraph) -> Any:  # noqa: ANN401
    return make_client(graph)[0].get(f"/api/v1/agent/{ASSISTANT}/run-definition", headers=as_user("bob"))


def _plain_chat(graph: InMemoryGraph, path: str) -> Any:  # noqa: ANN401
    return _chatbot_client(graph).post(f"/api/v1/{path}", headers=as_user("bob"), json={"query": "hi"})


@pytest.fixture
def loop(monkeypatch) -> list[dict[str, Any]]:
    """Records any run that starts; a refused caller starts none."""
    calls: list[dict[str, Any]] = []

    def fake(*args: object, **kwargs: object) -> Any:  # noqa: ANN401
        calls.append({"args": args, "kwargs": kwargs})

        async def gen() -> Any:  # noqa: ANN401
            yield "event: RUN_FINISHED\ndata: {}\n\n"

        return gen()

    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return object(), {"isMultimodal": False, "provider": "openai"}, {}

    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", fake)
    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    monkeypatch.setattr("app.api.routes.chatbot.run_chat_stream", fake)
    monkeypatch.setattr("app.api.routes.chatbot.get_llm_for_chat", llm)
    return calls


ENTRIES = {
    "assistant-stream": lambda g: _assistant_chat(g, "chat/stream"),
    "assistant-chat": lambda g: _assistant_chat(g, "chat"),
    "assistant-run-definition": _run_definition,
    "chat-stream": lambda g: _plain_chat(g, "chat/stream"),
    "chat": lambda g: _plain_chat(g, "chat"),
}


@pytest.mark.parametrize("entry", list(ENTRIES))
@pytest.mark.parametrize("state", ["removed", "disabled", "missing", "service"])
def test_an_account_that_is_not_a_live_person_is_refused(loop, entry, state) -> None:
    graph = InMemoryGraph()
    _switch_off(graph, state)

    response = ENTRIES[entry](graph)

    # The agent routes answer a missing record with their own 404, as for a named agent.
    if state == "missing" and entry.startswith("assistant"):
        assert response.status_code == 404
    else:
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == UNIDENTIFIED_CALLER_DETAIL
    assert not loop


@pytest.mark.parametrize("entry", list(ENTRIES))
def test_a_failed_read_of_the_callers_record_is_never_read_as_live(loop, entry) -> None:
    graph = InMemoryGraph()
    graph.fail("get_user_by_user_id")

    response = ENTRIES[entry](graph)

    assert response.status_code == 503, response.text
    assert not loop


@pytest.mark.parametrize("entry", ["chat-stream", "chat"])
def test_a_live_person_still_reaches_the_chat(loop, entry) -> None:
    ENTRIES[entry](InMemoryGraph())
    assert len(loop) == 1
