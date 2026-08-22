"""Tests for POST /api/v1/parse and GET /api/v1/parse/providers endpoints."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api.routes.parsing import router as parsing_router
from app.models.blocks import BlocksContainer
from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.parsing.registry import ParserRegistry
from app.services.resource_governor import Pool, ResourceGovernor
from app.services.resource_governor.models import ResourceSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_governor(*, heavy_limit: int = 5, light_limit: int | None = None) -> ResourceGovernor:
    """Build a real ``ResourceGovernor`` with a fixed probe so the heavy-parse
    ceiling is deterministic, mirroring parsing_main.py's lifespan setup.

    ``light_limit``, when given, is poked directly into the registry after
    construction — the light ceiling derives from ``cpu_quota`` (floored at
    8, see policy.py) and isn't independently settable via the constructor,
    same as ``test_consumer_concurrency_governor.py``'s pattern for pinning
    a specific pool's limit in tests.
    """
    snapshot = ResourceSnapshot(
        cpu_quota=4.0,
        cpu_utilisation=0.1,
        cpu_throttled_ratio=0.0,
        cpu_pressure=0.0,
        mem_limit_bytes=8 * 1024 ** 3,
        mem_working_set_bytes=1 * 1024 ** 3,
        source="test",
    )

    class _FixedProbe:
        def snapshot(self) -> ResourceSnapshot:
            return snapshot

    governor = ResourceGovernor(
        logger=logging.getLogger("test.parsing_routes.governor"),
        env_parse=heavy_limit,
        probe=_FixedProbe(),
    )
    if light_limit is not None:
        governor._registry.set(Pool.LIGHT_PARSE, light_limit)
    return governor


def _build_app(
    registry: ParserRegistry, heavy_limit: int = 5, light_limit: int | None = None
) -> FastAPI:
    app = FastAPI()
    app.state.parser_registry = registry
    # Mirrors the governor set up in parsing_main.py's lifespan.
    app.state.governor = _make_governor(heavy_limit=heavy_limit, light_limit=light_limit)
    app.include_router(parsing_router)

    @app.get("/health")
    async def health_check() -> dict:
        # Deliberately outside the parsing router / admission gates,
        # mirroring parsing_main.py's real health endpoint.
        return {"status": "ok"}

    return app


def _empty_bc() -> BlocksContainer:
    return BlocksContainer(blocks=[], block_groups=[])


def _ok_result() -> ParseResult:
    return ParseResult(
        block_container=_empty_bc(),
        provider_used=ParserProvider.DEFAULT,
        metadata={},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/parse — success
# ---------------------------------------------------------------------------


def test_parse_endpoint_success() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(return_value=_ok_result())
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.csv", b"a,b\n1,2", "text/csv")},
        data={"record_name": "test.csv", "mime_type": "text/csv", "extension": "csv", "provider": "default"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["provider_used"] == "default"
    mock_parser.parse.assert_awaited_once()


def test_parse_endpoint_with_explicit_provider() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(return_value=_ok_result())
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        data={
            "record_name": "test.pdf",
            "mime_type": "application/pdf",
            "extension": "pdf",
            "provider": "default",
        },
    )

    assert response.status_code == 200
    registry.resolve.assert_called_once()
    mock_parser.parse.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/v1/parse — error cases
# ---------------------------------------------------------------------------


def test_parse_endpoint_no_provider_returns_400() -> None:
    registry = MagicMock(spec=ParserRegistry)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.xyz", b"data", "application/octet-stream")},
        data={"mime_type": "application/octet-stream", "extension": "xyz"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NO_PROVIDER_PROVIDED"


def test_parse_endpoint_unsupported_format() -> None:
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(
        side_effect=ParseError(ParseErrorCode.UNSUPPORTED_FORMAT, "No parser for xyz")
    )

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.xyz", b"data", "application/octet-stream")},
        data={"mime_type": "application/octet-stream", "extension": "xyz", "provider": "default"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_parse_endpoint_parse_failed() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(
        side_effect=ParseError(ParseErrorCode.PARSE_FAILED, "Docling crashed")
    )
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"bad data", "application/pdf")},
        data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PARSE_FAILED"


def test_parse_endpoint_invalid_provider() -> None:
    registry = MagicMock(spec=ParserRegistry)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"data", "application/pdf")},
        data={"mime_type": "application/pdf", "provider": "nonexistent_provider"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False


def test_parse_endpoint_unexpected_exception() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(side_effect=RuntimeError("Boom"))
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"data", "application/pdf")},
        data={"mime_type": "application/pdf", "provider": "default"},
    )

    assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/v1/parse/providers
# ---------------------------------------------------------------------------


def test_list_providers_endpoint() -> None:
    registry = MagicMock(spec=ParserRegistry)
    registry.list_all_formats.return_value = {
        "pdf": ["docling", "default"],
        "csv": ["default"],
    }

    app = _build_app(registry)
    client = TestClient(app)

    response = client.get("/api/v1/parse/providers")

    assert response.status_code == 200
    body = response.json()
    assert "pdf" in body
    assert "csv" in body
    assert "docling" in body["pdf"]


# ---------------------------------------------------------------------------
# Concurrency gate
# ---------------------------------------------------------------------------


def _slow_parser(release_event: asyncio.Event, hold_seconds: float = 0.0) -> MagicMock:
    """A stub IParser whose .parse() blocks until *release_event* is set (or
    *hold_seconds* elapses, whichever comes first) so tests can control
    exactly how long a "slot" stays held.
    """
    mock_parser = MagicMock()

    async def _parse(*_args: object, **_kwargs: object) -> ParseResult:
        try:
            await asyncio.wait_for(release_event.wait(), timeout=hold_seconds or None)
        except asyncio.TimeoutError:
            pass
        return _ok_result()

    mock_parser.parse = _parse
    return mock_parser


@pytest.mark.asyncio
async def test_second_request_waits_then_succeeds_once_slot_frees(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the heavy-parse ceiling at 1, a second heavy (pdf) request queues
    behind the first and completes once the first releases its slot — it
    should not be rejected as long as the slot frees before the gate timeout.
    """
    import app.api.routes.parsing as parsing_routes

    monkeypatch.setattr(parsing_routes, "PARSE_QUEUE_WAIT_WARN_SECONDS", 0.05)
    monkeypatch.setattr(parsing_routes, "PARSE_GATE_TIMEOUT_SECONDS", 5.0)

    release_event = asyncio.Event()
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=_slow_parser(release_event))

    app = _build_app(registry, heavy_limit=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = asyncio.create_task(
            client.post(
                "/api/v1/parse",
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
            )
        )
        await asyncio.sleep(0.05)  # let the first request acquire the slot

        second = asyncio.create_task(
            client.post(
                "/api/v1/parse",
                files={"file": ("b.pdf", b"%PDF-1.4", "application/pdf")},
                data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
            )
        )
        await asyncio.sleep(0.1)  # second is now queued on the gate

        release_event.set()  # free the first request's slot
        first_response, second_response = await asyncio.gather(first, second)

    assert first_response.status_code == 200
    assert second_response.status_code == 200


@pytest.mark.asyncio
async def test_second_request_gets_429_backpressure_after_gate_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the heavy-parse ceiling at 1, a second request that can't get a
    slot within PARSE_GATE_TIMEOUT_SECONDS gets a retryable 429 with
    Retry-After and PARSE_BACKPRESSURE instead of hanging or a bare 503."""
    import app.api.routes.parsing as parsing_routes

    monkeypatch.setattr(parsing_routes, "PARSE_QUEUE_WAIT_WARN_SECONDS", 0.02)
    monkeypatch.setattr(parsing_routes, "PARSE_GATE_TIMEOUT_SECONDS", 0.1)

    release_event = asyncio.Event()  # held during the assertions, then set to let the first request drain before the client closes
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=_slow_parser(release_event, hold_seconds=1.0))

    app = _build_app(registry, heavy_limit=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = asyncio.create_task(
            client.post(
                "/api/v1/parse",
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
            )
        )
        await asyncio.sleep(0.05)  # let the first request acquire the slot

        second_response = await client.post(
            "/api/v1/parse",
            files={"file": ("b.pdf", b"%PDF-1.4", "application/pdf")},
            data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
        )

        release_event.set()
        await first

    assert second_response.status_code == 429
    assert second_response.headers["retry-after"] == "5"
    body = second_response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PARSE_BACKPRESSURE"
    assert body["error"]["details"]["tier"] == "heavy"


@pytest.mark.asyncio
async def test_light_request_proceeds_while_heavy_pool_saturated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A light (csv) request must not queue behind a saturated heavy pool —
    the two tiers are routed to independent gates so Jira/Confluence-shaped
    "blocks" payloads never starve behind a slow PDF/OCR parse."""
    import app.api.routes.parsing as parsing_routes

    monkeypatch.setattr(parsing_routes, "PARSE_QUEUE_WAIT_WARN_SECONDS", 5.0)
    monkeypatch.setattr(parsing_routes, "PARSE_GATE_TIMEOUT_SECONDS", 5.0)

    heavy_release = asyncio.Event()  # held during the assertions, then set to let the heavy request drain before the client closes
    light_parser = MagicMock()
    light_parser.parse = AsyncMock(return_value=_ok_result())

    def _resolve(_mime: str, extension: str, _provider: object) -> MagicMock:
        if extension == "pdf":
            return _slow_parser(heavy_release, hold_seconds=1.0)
        return light_parser

    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(side_effect=_resolve)

    app = _build_app(registry, heavy_limit=1, light_limit=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        heavy_task = asyncio.create_task(
            client.post(
                "/api/v1/parse",
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
            )
        )
        await asyncio.sleep(0.05)  # let the heavy request saturate the heavy-parse gate

        light_response = await asyncio.wait_for(
            client.post(
                "/api/v1/parse",
                files={"file": ("b.csv", b"a,b\n1,2", "text/csv")},
                data={"mime_type": "text/csv", "extension": "csv", "provider": "default"},
            ),
            timeout=1.0,
        )

        heavy_release.set()
        await heavy_task

    assert light_response.status_code == 200


@pytest.mark.asyncio
async def test_health_stays_responsive_while_parse_in_flight() -> None:
    """/health is not gated by the parsing admission gates, so it must
    respond even while the single heavy-parse slot is held by an in-flight
    request."""
    release_event = asyncio.Event()
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=_slow_parser(release_event, hold_seconds=1.0))

    app = _build_app(registry, heavy_limit=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        parse_task = asyncio.create_task(
            client.post(
                "/api/v1/parse",
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
            )
        )
        await asyncio.sleep(0.05)  # let the parse request acquire the slot and start "working"

        health_response = await asyncio.wait_for(client.get("/health"), timeout=1.0)
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        release_event.set()
        await parse_task
