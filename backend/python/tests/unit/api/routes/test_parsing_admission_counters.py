"""Each 429 reason of the parse route is counted once, on its own reason and tier, through the real route."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response

from app.api.routes import parsing as parsing_route
from app.services.parsing.registry import ParserRegistry
from app.services.resource_governor.lanes import LaneWaitTimeout
from tests.unit.api.routes.test_parsing_routes import _build_app, _slow_parser


def _delta(before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {t: {r: after[t][r] - before[t][r] for r in after[t] if after[t][r] != before[t][r]} for t in after
            if any(after[t][r] != before[t][r] for r in after[t])}


def _post(client: TestClient, name: str = "a.pdf", ext: str = "pdf", mime: str = "application/pdf") -> Response:
    return client.post("/api/v1/parse", files={"file": (name, b"%PDF-1.4", mime)},
                       data={"mime_type": mime, "extension": ext, "provider": "default"})


def test_a_lane_wait_timeout_counts_lane_timeout_only() -> None:
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()
    parser.parse = AsyncMock(side_effect=LaneWaitTimeout("no lane"))
    registry.resolve = MagicMock(return_value=parser)
    before = parsing_route.parse_admission_stats()["rejected"]
    with TestClient(_build_app(registry)) as client:
        r = client.post("/api/v1/parse", files={"file": ("a.docx", b"PK", "application/octet-stream")},
                        data={"mime_type": "application/octet-stream", "extension": "docx", "provider": "default"})
    assert r.status_code == 429
    d = _delta(before, parsing_route.parse_admission_stats()["rejected"])
    assert d == {"light": {"lane_timeout": 1}} or d == {"heavy": {"lane_timeout": 1}}, d


def test_a_share_refusal_counts_over_share_only(monkeypatch) -> None:
    monkeypatch.setattr(parsing_route._ORG_SHARE, "over_share", lambda *a, **k: True)
    registry = MagicMock(spec=ParserRegistry)
    before = parsing_route.parse_admission_stats()["rejected"]
    with TestClient(_build_app(registry)) as client:
        r = _post(client)
    assert r.status_code == 429
    assert _delta(before, parsing_route.parse_admission_stats()["rejected"]) == {"heavy": {"over_share": 1}}


def test_a_memory_wait_timeout_counts_memory_excess_only(monkeypatch) -> None:
    monkeypatch.setattr(parsing_route, "permit_excess_bytes", lambda *a, **k: 10**15)
    registry = MagicMock(spec=ParserRegistry)
    app = _build_app(registry)
    app.state.governor.memory_budget.reserve = AsyncMock(return_value=False)
    before = parsing_route.parse_admission_stats()["rejected"]
    with TestClient(app) as client:
        r = _post(client)
    assert r.status_code == 429, r.text
    assert _delta(before, parsing_route.parse_admission_stats()["rejected"]) == {"heavy": {"memory_excess": 1}}


@pytest.mark.asyncio
async def test_a_gate_wait_timeout_counts_gate_timeout_only(monkeypatch) -> None:
    monkeypatch.setattr(parsing_route, "PARSE_QUEUE_WAIT_WARN_SECONDS", 0.02)
    monkeypatch.setattr(parsing_route, "PARSE_GATE_TIMEOUT_SECONDS", 0.1)
    release = asyncio.Event()
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=_slow_parser(release, hold_seconds=1.0))
    app = _build_app(registry, heavy_limit=1)
    before = parsing_route.parse_admission_stats()["rejected"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        files = {"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
        data = {"mime_type": "application/pdf", "extension": "pdf", "provider": "default"}
        first = asyncio.create_task(client.post("/api/v1/parse", files=files, data=data))
        await asyncio.sleep(0.05)
        second = await client.post("/api/v1/parse", files=files, data=data)
        release.set()
        await first
    assert second.status_code == 429
    assert _delta(before, parsing_route.parse_admission_stats()["rejected"]) == {"heavy": {"gate_timeout": 1}}


