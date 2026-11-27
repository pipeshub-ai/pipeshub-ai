"""The assistant's chat routes, with ``app.edition.AGENTS_RUN_AS_THE_CALLER`` on, serve only a person acting as
themself: an OAuth app's user token passes, its client-credentials token is refused before the body is read.

The real chatbot router behind a real FastAPI app; only the token check (claims normalized as auth.py does) and the
container-backed dependencies are stubbed. A request that passes the gate is answered by the body check (400).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app import edition
from app.api.middlewares.auth import _normalize_regular_payload
from app.api.routes import chatbot

APP_TOKEN = {"orgId": "6650f47a00000000000000aa", "tokenType": "oauth", "scope": "conversation:chat"}
PERSON = "6650f47a0000000000000001"
USER_TOKEN = {**APP_TOKEN, "userId": PERSON, "client_id": "client-9"}
CLIENT_CREDENTIALS = {**APP_TOKEN, "userId": "client-9", "client_id": "client-9", "createdBy": PERSON}
BAD_BODY = {"query": "hi", "keywordSearchBudget": "neither"}


async def _placeholder() -> object:
    return object()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(edition, "AGENTS_RUN_AS_THE_CALLER", True)
    app = FastAPI()

    @app.middleware("http")
    async def claims_from_header(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = _normalize_regular_payload(json.loads(request.headers["x-claims"]), "t")
        return await call_next(request)

    app.include_router(chatbot.router, prefix="/api/v1")
    for dep in (chatbot.get_retrieval_service, chatbot.get_graph_provider, chatbot.get_config_service,
                chatbot.get_run_cancellation_registry):
        app.dependency_overrides[dep] = _placeholder
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/api/v1/chat/stream", "/api/v1/chat"])
def test_an_oauth_apps_user_token_passes_and_its_client_credentials_token_is_refused(
    client: TestClient, path: str
) -> None:
    def status(claims: dict) -> int:
        return client.post(path, content=json.dumps(BAD_BODY), headers={"x-claims": json.dumps(claims)}).status_code

    assert status(USER_TOKEN) == 400
    assert status(CLIENT_CREDENTIALS) == 403
