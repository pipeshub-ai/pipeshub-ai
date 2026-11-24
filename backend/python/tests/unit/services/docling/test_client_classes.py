"""The Docling client under ``PARSE_ADMISSION=classes``: its calls go through a limiter, carry the class
and (for a small file) a deadline, and a 429 slows Docling calls only (no coordinator signal, so no consumer is paused
and the governor's downstream feedback sees no incident). With classes off nothing changes.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.capacity.classes import CostClass
from app.modules.parse_engine import pools, wiring
from app.modules.parse_engine.engines import (
    reset_shared_docling_engine,
    shared_docling_engine,
)
from app.services.docling.client import PARSE_DEADLINE_HEADER, DoclingClient
from app.services.messaging.backpressure import (
    get_default_backpressure_coordinator,
    set_default_backpressure_coordinator,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

PDF = b"%PDF-1.4 minimal"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    monkeypatch.delenv("PARSE_LANES", raising=False)
    monkeypatch.setenv("PARSE_DOCLING_WIDTH", "4")
    reset_shared_docling_engine()
    wiring.reset_for_tests()
    previous = get_default_backpressure_coordinator()
    coordinator = MagicMock()
    set_default_backpressure_coordinator(coordinator)
    yield coordinator
    set_default_backpressure_coordinator(previous)
    reset_shared_docling_engine()
    pools.set_class(None)


def _responses() -> list[httpx.Response]:
    ok = httpx.Response(200, json={"success": True, "parse_result": "{}"}, request=httpx.Request("POST", "http://x"))
    throttled = httpx.Response(429, headers={"Retry-After": "1"}, request=httpx.Request("POST", "http://x"))
    return [throttled, ok]


async def _parse(client: DoclingClient) -> tuple[str | None, list[dict[str, str]]]:
    sent: list[dict[str, str]] = []
    queue = _responses()

    async def request(_self: object, _method: str, _url: str, **kwargs: object) -> httpx.Response:
        sent.append(dict(kwargs["headers"]))  # type: ignore[arg-type]
        return queue.pop(0)

    with patch.object(httpx.AsyncClient, "request", request), patch("app.services.base_client.asyncio.sleep", AsyncMock()):
        return await client.parse_pdf("a.pdf", PDF), sent


async def test_a_docling_429_slows_docling_only_under_classes(monkeypatch: pytest.MonkeyPatch, _fresh: MagicMock) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    pools.set_class(CostClass.SMALL)
    client = DoclingClient(service_url="http://docling.test")
    engine = shared_docling_engine()
    result, sent = await _parse(client)
    assert result == "{}"
    _fresh.signal.assert_not_called()  # no consumer is paused, the governor sees no throttle
    assert engine.throttle_count == 1 and engine.limit == 2  # 4 halved
    assert engine.in_flight == 0


async def test_the_call_carries_its_class_and_a_deadline_for_a_small_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    pools.set_class(CostClass.SMALL)
    _, sent = await _parse(DoclingClient(service_url="http://docling.test"))
    assert sent[-1]["X-Parse-Class"] == "small"
    assert 60_000 <= int(sent[-1][PARSE_DEADLINE_HEADER]) <= 120_000


async def test_the_deadline_follows_the_estimated_parse_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """min(120, max(60, 4 x estimate)): 20 s of parse is a deadline of 80 s, not a fixed 60 s."""
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    pools.set_class(CostClass.SMALL, 20.0)
    _, sent = await _parse(DoclingClient(service_url="http://docling.test"))
    assert int(sent[-1][PARSE_DEADLINE_HEADER]) == 80_000


async def test_a_big_file_carries_its_class_and_no_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    pools.set_class(CostClass.LARGE)
    _, sent = await _parse(DoclingClient(service_url="http://docling.test"))
    assert sent[-1]["X-Parse-Class"] == "large" and PARSE_DEADLINE_HEADER not in sent[-1]


async def test_calls_never_exceed_the_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.setenv("PARSE_DOCLING_WIDTH", "2")
    reset_shared_docling_engine()
    client = DoclingClient(service_url="http://docling.test")
    engine = shared_docling_engine()
    ok = httpx.Response(200, json={"success": True, "parse_result": "{}"}, request=httpx.Request("POST", "http://x"))

    async def request(_self: object, *_a: object, **_k: object) -> httpx.Response:
        await asyncio.sleep(0.05)
        return ok

    with patch.object(httpx.AsyncClient, "request", request):
        await asyncio.gather(*(client.parse_pdf(f"{i}.pdf", PDF) for i in range(6)))
    assert engine.max_in_flight == 2


async def test_without_classes_a_429_still_signals_the_coordinator_and_nothing_is_added(_fresh: MagicMock) -> None:
    client = DoclingClient(service_url="http://docling.test")
    assert client._limiter is None and client.on_backpressure is None
    result, sent = await _parse(client)
    assert result == "{}"
    _fresh.signal.assert_called_once()
    assert "X-Parse-Class" not in sent[-1] and PARSE_DEADLINE_HEADER not in sent[-1]


async def test_a_retry_after_a_retry_after_wait_is_admitted_again_at_the_reduced_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Four admitted calls are all told to wait: their retries must not all go out at the limit they started with."""
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    pools.set_class(CostClass.SMALL)
    engine = shared_docling_engine()
    client = DoclingClient(service_url="http://docling.test")
    ok = httpx.Response(200, json={"success": True, "parse_result": "{}"}, request=httpx.Request("POST", "http://x"))
    throttled = httpx.Response(429, headers={"Retry-After": "1"}, request=httpx.Request("POST", "http://x"))
    calls = {"n": 0}
    over: list[tuple[int, int]] = []
    async def request(_self: object, _method: str, _url: str, **_kw: object) -> httpx.Response:
        calls["n"] += 1
        await asyncio.sleep(0.01)  # a real call is not instantaneous: the others are in flight too
        if calls["n"] > 4 and engine.in_flight > engine.limit:
            over.append((engine.in_flight, engine.limit))
        return throttled if calls["n"] <= 4 else ok

    with patch.object(httpx.AsyncClient, "request", request):  # the Retry-After wait (about a second) is real
        results = await asyncio.gather(*(client.parse_pdf(f"{i}.pdf", PDF) for i in range(4)))
    assert results == ["{}"] * 4
    assert engine.limit < 4  # the throttles reduced it
    assert over == []  # every retry was admitted at the reduced limit, not at the one the call began with
    assert engine.in_flight == 0
