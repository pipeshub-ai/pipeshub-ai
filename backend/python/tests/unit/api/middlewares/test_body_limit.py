"""BodyLimitMiddleware: 413 before the body is buffered."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.middlewares.body_limit import BodyLimitMiddleware


def _app(limit: int) -> FastAPI:
    app = FastAPI()

    @app.post("/capped")
    async def capped(payload: dict) -> dict:
        return {"keys": len(payload)}

    @app.post("/free")
    async def free(payload: dict) -> dict:
        return {"keys": len(payload)}

    app.add_middleware(BodyLimitMiddleware, limits={"/capped": limit})
    return app


async def _post(app: FastAPI, path: str, **kw: object) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(path, **kw)


@pytest.mark.asyncio
async def test_declared_oversize_is_413() -> None:
    r = await _post(_app(100), "/capped", json={"a": "x" * 500})
    assert r.status_code == 413
    assert "too large" in r.json()["detail"]


@pytest.mark.asyncio
async def test_small_body_passes() -> None:
    r = await _post(_app(100), "/capped", json={"a": 1})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_other_paths_are_not_capped() -> None:
    r = await _post(_app(100), "/free", json={"a": "x" * 500})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_chunked_oversize_is_413() -> None:
    app = _app(100)
    chunks = [b'{"a":"', b"x" * 200, b'"}']

    async def gen() -> AsyncIterator[bytes]:
        for c in chunks:
            yield c

    r = await _post(app, "/capped", content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_declared_oversize_never_reads_the_body() -> None:
    sent: list[dict] = []

    async def app(scope: dict, receive: object, send: object) -> None:
        raise AssertionError("app must not run")

    async def receive() -> dict:
        raise AssertionError("body must not be read")

    async def send(message: dict) -> None:
        sent.append(message)

    mw = BodyLimitMiddleware(app, {"/capped": 10})
    await mw(
        {"type": "http", "method": "POST", "path": "/capped", "headers": [(b"content-length", b"11")]},
        receive, send,
    )
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_of_exactly_the_limit_passes_and_one_more_is_413() -> None:
    app = _app(60)
    ok = await _post(app, "/capped", content=b'{"a":"' + b"x" * 52 + b'"}', headers={"content-type": "application/json"})
    assert len(b'{"a":"' + b"x" * 52 + b'"}') == 60
    assert ok.status_code == 200
    over = await _post(app, "/capped", content=b'{"a":"' + b"x" * 53 + b'"}', headers={"content-type": "application/json"})
    assert over.status_code == 413
