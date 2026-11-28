"""The agent loop refuses a run that no route guard verified for the request.

`require_identified_caller` is a dependency of every agent-loop route. The loop's
entry points (`run_agent_loop_stream`, `run_chat_stream`) check, before anything
else, that it ran for the current request and verified the person the loop runs
as. So a route that re-registers a handler without the guard (a mounted sub-app,
a lambda or partial endpoint, a new main) fails closed at run time, even where
the structural test (`test_agent_loop_entries_refuse_unidentified.py`) cannot see
the shape.

"The loop started" is observed at the first thing each entry does after its
check: it creates the run's `CancellationToken`, which is replaced here by a
probe that records the run and stops it.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.agents.agent_loop.stream_bridge import run_agent_loop_stream, run_chat_stream
from app.api.middlewares.identified_caller import (
    UNIDENTIFIED_CALLER_DETAIL,
    loop_caller_refusal,
    require_identified_caller,
)
from app.api.middlewares.request_context import RequestContextMiddleware
from app.api.routes import chatbot
from tests.support.agent_routes import InMemoryGraph, _TestAuth, as_user, make_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

ORG = "org-1"
USERS = {
    "bob": {
        "userId": "u-bob",
        "orgId": ORG,
        "email": "bob@acme.test",
        "fullName": "Bob",
    },
    "alice": {"userId": "u-alice", "orgId": ORG, "email": "alice@acme.test"},
    "app": {
        "userId": "client-1",
        "orgId": ORG,
        "principalKind": "app",
        "appId": "client-1",
    },
}
_log = logging.getLogger(__name__)


class _LoopStarted(Exception):
    pass


class LoopProbe:
    """Stands in for `CancellationToken`, the first thing each entry creates after its check."""

    def __init__(self) -> None:
        self.started = 0

    def __call__(self) -> None:
        self.started += 1
        raise _LoopStarted


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> LoopProbe:
    probe = LoopProbe()
    monkeypatch.setattr("app.agents.agent_loop.stream_bridge.CancellationToken", probe)
    return probe


ENTRIES: dict[str, Callable[..., AsyncIterator[str]]] = {
    "agent": lambda user: run_agent_loop_stream(
        {"query": "hi"}, user, MagicMock(), _log, None, None, None, None
    ),
    "assistant": lambda user: run_chat_stream(
        {"query": "hi"}, user, MagicMock(), None, _log,
        retrieval_service=None, graph_provider=None, reranker_service=None, config_service=None,
    ),
}


async def _run(entry: str, user: dict[str, Any]) -> str:
    """'started', or 'refused' when the entry streamed only the public refusal."""
    try:
        frames = [frame async for frame in ENTRIES[entry](user)]
    except _LoopStarted:
        return "started"
    assert len(frames) == 1 and UNIDENTIFIED_CALLER_DETAIL in frames[0], frames
    assert "unidentified_caller" in frames[0]
    return "refused"


def _request(user: dict[str, Any]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "state": {"user": dict(user)},
        }
    )


# --- the entry points themselves --------------------------------------------------------------------


@pytest.mark.parametrize("entry", ENTRIES)
async def test_each_entry_refuses_when_no_route_verified_the_caller(
    probe: LoopProbe, entry: str
) -> None:
    assert await _run(entry, USERS["bob"]) == "refused"
    assert probe.started == 0


@pytest.mark.parametrize("entry", ENTRIES)
async def test_each_entry_runs_for_the_person_the_route_verified(
    probe: LoopProbe, entry: str
) -> None:
    await require_identified_caller(_request(USERS["bob"]))
    assert await _run(entry, USERS["bob"]) == "started"


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize(
    "runs_as",
    [
        USERS["alice"],
        {**USERS["bob"], "orgId": "org-2"},
        {"orgId": ORG},
        {},
    ],
)
async def test_each_entry_refuses_to_run_as_anyone_but_the_verified_person(
    probe: LoopProbe,
    entry: str,
    runs_as: dict[str, Any],
) -> None:
    await require_identified_caller(_request(USERS["bob"]))
    assert await _run(entry, runs_as) == "refused"
    assert probe.started == 0


@pytest.mark.parametrize("entry", ENTRIES)
async def test_a_principal_the_guard_refuses_is_never_verified(
    probe: LoopProbe, entry: str
) -> None:
    with pytest.raises(HTTPException):
        await require_identified_caller(_request(USERS["app"]))
    assert await _run(entry, USERS["app"]) == "refused"
    assert probe.started == 0


def test_the_check_without_a_route_refuses() -> None:
    assert loop_caller_refusal(USERS["bob"]) == UNIDENTIFIED_CALLER_DETAIL


# --- a verification belongs to one request ----------------------------------------------------------


def _probe_app(*, request_context: bool = False) -> FastAPI:
    app = FastAPI()

    @app.post("/verify", dependencies=[Depends(require_identified_caller)])
    async def verify() -> dict[str, bool]:
        return {"ok": True}

    async def run(request: Request) -> dict[str, str]:
        return {"result": await _run("assistant", request.state.user)}

    app.add_api_route("/run", run, methods=["POST"])
    app.add_api_route(
        "/guarded-run",
        run,
        methods=["POST"],
        dependencies=[Depends(require_identified_caller)],
    )
    app.add_middleware(_TestAuth, users=USERS)
    if request_context:
        app.add_middleware(RequestContextMiddleware)
    return app


def test_a_verification_does_not_outlive_its_request(probe: LoopProbe) -> None:
    client = TestClient(_probe_app())
    assert client.post("/verify", headers=as_user("bob")).status_code == 200
    assert client.post("/run", headers=as_user("bob")).json() == {"result": "refused"}
    assert client.post("/guarded-run", headers=as_user("bob")).json() == {
        "result": "started"
    }
    assert client.post("/run", headers=as_user("bob")).json() == {"result": "refused"}
    assert probe.started == 1


async def test_a_later_request_served_on_the_same_task_does_not_inherit_it(
    probe: LoopProbe,
) -> None:
    # httpx's ASGI transport runs every request in the calling task, so only the per-request trace
    # context (set by RequestContextMiddleware, which every service app installs) tells them apart.
    transport = httpx.ASGITransport(app=_probe_app(request_context=True))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/verify", headers=as_user("bob"))).status_code == 200
        assert (await client.post("/run", headers=as_user("bob"))).json() == {
            "result": "refused"
        }
        assert (await client.post("/guarded-run", headers=as_user("bob"))).json() == {
            "result": "started"
        }
    assert probe.started == 1


async def test_concurrent_requests_do_not_share_a_verification(
    probe: LoopProbe,
) -> None:
    verified, ran = asyncio.Event(), asyncio.Event()

    async def guarded() -> str:
        await require_identified_caller(_request(USERS["bob"]))
        verified.set()
        await ran.wait()
        return await _run("agent", USERS["bob"])

    async def unguarded() -> str:
        await verified.wait()
        try:
            return await _run("agent", USERS["bob"])
        finally:
            ran.set()

    assert await asyncio.gather(guarded(), unguarded()) == ["started", "refused"]


async def test_a_stream_started_in_a_task_inherits_its_own_requests_verification(
    probe: LoopProbe,
) -> None:
    await require_identified_caller(_request(USERS["bob"]))
    # The response body is iterated in a task the request spawns, which copies the request's context.
    assert await asyncio.create_task(_run("assistant", USERS["bob"])) == "started"


# --- route shapes that re-register a guarded handler without its guard --------------------------------


def _req(request: Request) -> Request:
    return request


def _assistant_app(build: Callable[[FastAPI], list[FastAPI]]) -> FastAPI:
    """A service app whose routes `build` adds; every app it returns gets the assistant's stand-ins."""
    app = FastAPI()
    registry = AsyncMock()
    registry.is_active = AsyncMock(return_value=False)
    config = AsyncMock()
    config.get_config.return_value = {"providers": []}
    for target in [app, *build(app)]:
        target.container = MagicMock()
        target.container.logger.return_value = _log
        target.dependency_overrides.update(
            {
                chatbot.get_retrieval_service: lambda: AsyncMock(),
                chatbot.get_graph_provider: lambda: AsyncMock(),
                chatbot.get_config_service: lambda: config,
                chatbot.get_run_cancellation_registry: lambda: registry,
            }
        )
    app.add_middleware(_TestAuth, users=USERS)
    return app


def _guarded_router(app: FastAPI) -> list[FastAPI]:
    app.include_router(chatbot.router, prefix="/api/v1")
    return []


def _mounted_sub_app(app: FastAPI) -> list[FastAPI]:
    sub = FastAPI()
    sub.add_api_route("/chat/stream", chatbot.askAIStream, methods=["POST"])
    app.mount("/api/v1/mounted", sub)
    return [sub]


def _another_main(app: FastAPI) -> list[FastAPI]:
    # What a main outside the scanned mains would do: register the handler on its own app.
    app.add_api_route(
        "/api/v1/other/chat/stream", chatbot.askAIStream, methods=["POST"]
    )
    return []


def _partial_endpoint(app: FastAPI) -> list[FastAPI]:
    app.add_api_route(
        "/api/v1/partial/chat/stream",
        functools.partial(chatbot.askAIStream),
        methods=["POST"],
    )
    return []


def _lambda_endpoint(app: FastAPI) -> list[FastAPI]:
    router = APIRouter()
    router.add_api_route(
        "/api/v1/lambda/chat/stream",
        lambda req=Depends(_req), rs=Depends(chatbot.get_retrieval_service), gp=Depends(chatbot.get_graph_provider), cs=Depends(chatbot.get_config_service), cr=Depends(chatbot.get_run_cancellation_registry): (
            chatbot.askAIStream(req, rs, gp, cs, cr)
        ),
        methods=["POST"],
    )
    app.include_router(router)
    return []


@pytest.fixture
def assistant_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return (
            MagicMock(),
            {"provider": "openai", "isMultimodal": False, "contextLength": 128000},
            {},
        )

    monkeypatch.setattr("app.api.routes.chatbot.get_llm_for_chat", llm)
    monkeypatch.setattr(
        "app.utils.execute_query.has_sql_connector_configured",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.utils.fetch_slack_thread.has_slack_connector_configured",
        AsyncMock(return_value=False),
    )


def _post(app: FastAPI, path: str, who: str) -> httpx.Response:
    return TestClient(app, raise_server_exceptions=False).post(
        path, headers=as_user(who), json={"query": "hi"}
    )


@pytest.mark.usefixtures("assistant_llm")
def test_the_guarded_assistant_route_reaches_the_loop_for_a_person(
    probe: LoopProbe,
) -> None:
    # Control: with the route guard in place, the same harness gets as far as the loop.
    _post(_assistant_app(_guarded_router), "/api/v1/chat/stream", "bob")
    assert probe.started == 1


@pytest.mark.usefixtures("assistant_llm")
@pytest.mark.parametrize(
    ("build", "path"),
    [
        (_mounted_sub_app, "/api/v1/mounted/chat/stream"),
        (_another_main, "/api/v1/other/chat/stream"),
        (_partial_endpoint, "/api/v1/partial/chat/stream"),
    ],
    ids=["mounted-sub-app", "another-main", "partial-endpoint"],
)
@pytest.mark.parametrize("who", ["bob", "app"])
def test_a_handler_registered_without_its_guard_is_refused_by_the_loop(
    probe: LoopProbe,
    build: Callable[[FastAPI], list[FastAPI]],
    path: str,
    who: str,
) -> None:
    response = _post(_assistant_app(build), path, who)
    assert probe.started == 0
    assert UNIDENTIFIED_CALLER_DETAIL in response.text
    assert "u-bob" not in response.text


@pytest.mark.usefixtures("assistant_llm")
@pytest.mark.parametrize("who", ["bob", "app"])
def test_a_lambda_endpoint_never_runs_the_loop(probe: LoopProbe, who: str) -> None:
    response = _post(
        _assistant_app(_lambda_endpoint), "/api/v1/lambda/chat/stream", who
    )
    assert probe.started == 0
    assert "RUN_FINISHED" not in response.text


@pytest.mark.parametrize("agent", ["agentIdPlaceholder", "shared", "sa"])
def test_the_guarded_agent_routes_reach_the_loop_for_a_person(
    probe: LoopProbe, monkeypatch: pytest.MonkeyPatch, agent: str
) -> None:
    # Control for the agent routes: with the route guard in place, the real chat route gets as far as the loop.
    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return MagicMock(), {"isMultimodal": False, "provider": "openai"}, {}

    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    graph = InMemoryGraph()
    graph.add_agent("shared", "alice", share_with_org=True)
    graph.add_agent("sa", "alice", share_with_org=True, isServiceAccount=True)
    client, _ = make_client(graph)
    for path in ("chat/stream", "chat"):
        client.post(
            f"/api/v1/agent/{agent}/{path}",
            headers=as_user("bob"),
            json={"query": "hi"},
        )
    assert probe.started == 2
