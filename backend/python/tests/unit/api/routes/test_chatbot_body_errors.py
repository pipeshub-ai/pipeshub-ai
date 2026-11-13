"""A chatbot route that refuses a body says which field and rule failed, never the value the caller sent (F47).

The real chatbot router is served by a real FastAPI app with the request-logging middleware, as in the query service.
Only the token check (claims come from a header) and the container-backed dependencies are stubbed: every route
resolves them before it reads its body, and none is used on the 4xx path.
"""

import json
from collections.abc import Awaitable, Callable, Iterator

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from pydantic import field_validator

from app.api.middlewares.request_log import install_request_logging
from app.api.routes import chatbot

SEED = "F47SEED-ada@example.com"
SESSION = {"userId": "6650f47a0000000000000001", "orgId": "6650f47a00000000000000aa"}
SERVICE = {**SESSION, "token_type": "scoped", "scopes": ["conversation:permissions"]}


async def _placeholder() -> object:
    return object()


def _app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def claims_from_header(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = json.loads(request.headers["x-claims"])
        return await call_next(request)

    install_request_logging(app)
    app.include_router(chatbot.router, prefix="/api/v1")
    for dep in (chatbot.get_retrieval_service, chatbot.get_graph_provider, chatbot.get_config_service,
                chatbot.get_run_cancellation_registry):
        app.dependency_overrides[dep] = _placeholder
    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def _send(client: TestClient, path: str, body: object, claims: dict = SESSION, method: str = "POST") -> tuple[int, str]:
    response = client.request(method, path, content=json.dumps(body), headers={"x-claims": json.dumps(claims)})
    return response.status_code, response.text


_ITEM = {"fileName": f"{SEED}.pdf", "mimeType": "application/pdf", "contentBase64": "RjQ3U0VFRA=="}


@pytest.mark.parametrize(
    ("method", "path", "claims", "body", "detail"),
    [
        ("POST", "/api/v1/chat/stream", SESSION, {"query": "hi", "projectInstructions": SEED + "x" * 8000},
         "Invalid request parameters: projectInstructions: String should have at most 8000 characters"),
        ("POST", "/api/v1/chat", SESSION, {"query": "hi", "keywordSearchBudget": SEED},
         "Invalid request parameters: keywordSearchBudget: Input should be 'short' or 'full'"),
        ("POST", "/api/v1/chat/attachments/upload", SESSION, {"attachments": [_ITEM]},
         "Invalid attachment upload payload: attachments.0.size: Field required"),
        *[(method, "/api/v1/chat/attachments/permissions", SERVICE, {"userIds": ["u2"], "recordIds": SEED},
           "Invalid request payload: recordIds: Input should be a valid list") for method in ("POST", "DELETE")],
        *[(method, "/api/v1/chat/artifacts/permissions", SERVICE, {"conversationId": "c1", "userIds": SEED},
           "Invalid request payload: userIds: Input should be a valid list") for method in ("POST", "DELETE")],
        ("POST", "/api/v1/chat/records/access", SERVICE, {"webUrls": [f"https://{SEED}/doc?token=t"] * 501},
         "Invalid request payload: webUrls: List should have at most 500 items after validation, not 501"),
        ("POST", "/api/v1/chat/cancel", SESSION, {"runId": SEED},
         "Invalid request parameters: runId: Invalid runId: must be a UUID."),
    ],
)
def test_a_refused_body_names_the_field_and_rule_but_not_the_value(
    client: TestClient, method: str, path: str, claims: dict, body: dict, detail: str
) -> None:
    status, text = _send(client, path, body, claims, method)
    assert status == 400
    assert json.loads(text) == {"detail": detail}
    assert "F47SEED" not in text


def test_a_body_that_is_not_an_object_is_still_a_400(client: TestClient) -> None:
    assert _send(client, "/api/v1/chat/stream", [SEED]) == (
        400, json.dumps({"detail": "Invalid request parameters: Input should be a valid dictionary or instance of ChatQuery"},
                        separators=(",", ":")),
    )


def test_a_body_that_is_not_json_is_unchanged(client: TestClient) -> None:
    response = client.post("/api/v1/chat/cancel", content=b"{not json", headers={"x-claims": json.dumps(SESSION)})
    assert (response.status_code, response.json()) == (400, {"detail": "Invalid JSON in request body"})


class _BrokenChatQuery(chatbot.ChatQuery):
    @field_validator("query")
    @classmethod
    def _bug(cls, value: str) -> str:
        raise RuntimeError("a bug in a validator")


def test_a_validator_bug_is_a_500_not_a_400(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # A bug is ours: it must not be answered as the caller's mistake, with its text in the body.
    monkeypatch.setattr(chatbot, "ChatQuery", _BrokenChatQuery)
    assert _send(client, "/api/v1/chat/stream", {"query": "hi"}) == (500, '{"detail":"Internal server error"}')
