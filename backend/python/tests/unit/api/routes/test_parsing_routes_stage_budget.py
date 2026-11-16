"""The parse route never cuts a parse the indexing client still waits for, and answers only its own deadline as 504.

Time is scaled by 1/2400: the 2400 s stage floor becomes 1.0 s and the 5400 s PDF record budget 2.25 s (the client's
read timeout for a PDF, 0.8 of it, 1.8 s). A large filing parsed in page batches that takes 1.4 s (3360 s) must index.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.services.parsing.client import ParsingClient
from app.services.parsing.interface import ParserProvider
from app.services.parsing.registry import ParserRegistry
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result

if TYPE_CHECKING:
    import pytest

SCALE = 1 / 2400


def _client_for(parse: Any, monkeypatch: pytest.MonkeyPatch) -> ParsingClient:  # noqa: ANN401
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()
    parser.parse = parse
    registry.resolve = MagicMock(return_value=parser)
    app = _build_app(registry)
    client = ParsingClient(service_url="http://parsing.test", retry_delay=0.01)
    monkeypatch.setattr(
        client, "_make_client",
        lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://parsing.test", timeout=30),
    )
    monkeypatch.setattr(client, "_auth_headers", AsyncMock(return_value={}))
    return client


async def test_a_pdf_parse_inside_its_own_budget_is_not_cut_at_the_stage_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache import locks

    monkeypatch.setattr(locks, "STAGE_DEADLINE_S", 2400 * SCALE)
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", str(5400 * SCALE))
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", str(1800 * SCALE))

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(3360 * SCALE)
        return _ok_result()

    client = _client_for(parse, monkeypatch)
    result = await client.parse(
        file_content=b"%PDF-1.7 a large filing", record_name="filing.pdf", mime_type="application/pdf",
        extension="pdf", provider=ParserProvider.DEFAULT,
    )
    assert result.block_container is not None


def test_the_route_deadline_is_never_shorter_than_the_clients_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.config import messaging_env
    from app.services.paid_cache import locks
    from app.services.parsing.client import PARSE_SHARE_OF_RECORD_BUDGET

    for pdf in ("900", "5400", "9000", "21600"):
        monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", pdf)
        assert locks.stage_deadline_s() >= messaging_env.pdf_record_processing_timeout * PARSE_SHARE_OF_RECORD_BUDGET
        assert locks.stage_deadline_s() >= messaging_env.record_processing_timeout * PARSE_SHARE_OF_RECORD_BUDGET
