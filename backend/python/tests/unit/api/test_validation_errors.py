"""FastAPI's 422 body without the rejected input, on every service that answers with FastAPI's default (F47)."""

import importlib
import json

import pytest
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from app.api.validation_errors import input_free_422

SEED = "F47SEED-ada@example.com"


class _Named(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _known(cls, value: str) -> str:
        raise ValueError("Invalid name")


def test_the_body_is_fastapi_s_default_without_the_input() -> None:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, input_free_422)

    @app.get("/x")
    async def _route(page: int = Query(1, ge=1)) -> None:  # pragma: no cover - never reached
        return None

    @app.post("/y")
    async def _body_route(body: _Named) -> None:  # pragma: no cover - never reached
        return None

    for query, expected in [
        (f"page={SEED}", {"type": "int_parsing", "loc": ["query", "page"],
                          "msg": "Input should be a valid integer, unable to parse string as an integer"}),
        ("page=0", {"type": "greater_than_equal", "loc": ["query", "page"], "msg": "Input should be greater than or equal to 1",
                    "ctx": {"ge": 1}}),
    ]:
        response = TestClient(app).get(f"/x?{query}")
        assert (response.status_code, response.json()) == (422, {"detail": [expected]})
        assert "F47SEED" not in response.text

    # A validator's ValueError sits in ctx.error: only jsonable_encoder makes it JSON ({}), not a 500.
    response = TestClient(app).post("/y", json={"name": SEED})
    assert (response.status_code, response.json()) == (422, {"detail": [
        {"type": "value_error", "loc": ["body", "name"], "msg": "Value error, Invalid name", "ctx": {"error": {}}}
    ]})
    assert "F47SEED" not in response.text


@pytest.mark.parametrize(
    "module",
    [
        "app.connectors_main",
        "app.indexing_main",
        "app.docling_main",
        "app.embedding_main",
        "app.extraction_main",
        "app.parsing_main",
    ],
)
def test_every_service_without_its_own_handler_registers_it(module: str) -> None:
    app = importlib.import_module(module).app
    assert app.exception_handlers[RequestValidationError] is input_free_422


def test_docling_s_mounted_app_registers_it_too() -> None:
    # docling_main mounts the service app at /: the service's routes answer with the mounted app's handlers.
    from app.docling_main import docling_app

    assert docling_app.exception_handlers[RequestValidationError] is input_free_422


def test_the_connectors_service_answers_a_bad_query_parameter_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import connectors_main
    from app.api.middlewares import auth
    from app.connectors.sources.localKB.api import kb_router

    claims = {"userId": "6650f47a0000000000000001", "orgId": "6650f47a00000000000000aa"}

    async def verified(request: Request) -> Request:  # the JWT check is the only seam
        request.state.user = claims
        auth._bind_org_context(request, claims)
        return request

    async def placeholder() -> object:
        return object()

    monkeypatch.setattr(connectors_main, "authMiddleware", verified)
    for dep in (connectors_main.get_initialized_container, kb_router.get_kb_service, kb_router.keep_path_inside_connection):
        monkeypatch.setitem(connectors_main.app.dependency_overrides, dep, placeholder)

    response = TestClient(connectors_main.app).get(f"/api/v1/kb/?page={SEED}", headers={"x-claims": json.dumps(claims)})
    assert response.status_code == 422
    assert response.json() == {"detail": [{"type": "int_parsing", "loc": ["query", "page"],
                                           "msg": "Input should be a valid integer, unable to parse string as an integer"}]}
