"""A query embedding the provider keeps rate limiting, end to end: a local server answers every call with a 429, the real
OpenAI client and the real retry run against it, and search answers a 503 that says to try again, not an unexplained 500."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chat_modes import prefetch
from app.api.routes.search import _failed_search_headers
from app.exceptions.fastapi_responses import Status
from app.modules.retrieval import retrieval_service as retrieval
from app.utils.aimodels import get_embedding_model
from tests.unit.modules.retrieval import test_source_diversity as search_world

if TYPE_CHECKING:
    from collections.abc import Iterator

_PROVIDER_WORDS = "Rate limit reached for gateway-key-team-7 on tokens per min"


class _Provider:
    """An OpenAI-compatible embeddings endpoint. Refuses the first ``refusals`` calls, then answers."""

    def __init__(self) -> None:
        self.status = 429
        self.headers: dict[str, str] = {}
        self.error: dict[str, Any] = {"message": _PROVIDER_WORDS, "type": "tokens", "code": "rate_limit_exceeded"}
        self.refusals = 10**6
        self.calls = 0
        world = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                world.calls += 1
                refused = world.calls <= world.refusals
                body = json.dumps(
                    {"error": world.error} if refused else
                    {"object": "list", "model": "m", "usage": {"prompt_tokens": 1, "total_tokens": 1},
                     "data": [{"object": "embedding", "index": 0, "embedding": [0.1] * 4}]},
                ).encode()
                self.send_response(world.status if refused else 200)
                for name, value in (world.headers if refused else {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def provider() -> Iterator[_Provider]:
    server = _Provider()
    try:
        yield server
    finally:
        server.close()


async def _search(provider: _Provider, sleep: AsyncMock | None = None) -> dict[str, Any]:
    service = search_world._service(search_world._FakeStore())
    service.get_embedding_model_instance = AsyncMock(return_value=get_embedding_model("openAI", {
        "provider": "openAI",
        "configuration": {"model": "text-embedding-3-small", "apiKey": "sk-test", "endpoint": provider.url},
    }, sdk_retries=0))
    service._cached_embedding_is_local = False
    service.logger = logging.getLogger("test.search")
    retrieval._user_cache.clear()
    with patch.object(retrieval, "describe_search_records", AsyncMock()), \
            patch.object(asyncio, "sleep", sleep or AsyncMock()):
        return await service.search_with_filters(["quarterly plan"], "alice", search_world.ORG, limit=5)


async def test_a_rate_limited_query_embedding_answers_503_try_again(
    provider: _Provider, caplog: pytest.LogCaptureFixture,
) -> None:
    provider.headers = {"Retry-After": "12"}
    with caplog.at_level(logging.INFO):
        result = await _search(provider)

    assert provider.calls == retrieval._RETRIEVAL_EMBED_MAX_RETRIES
    assert result["status"] == Status.EMBEDDING_RATE_LIMITED.value
    assert result["status_code"] == 503
    assert result["message"] == retrieval.EMBEDDING_RATE_LIMITED_MESSAGE
    assert result["searchResults"] == []
    assert _failed_search_headers(result) == {"Retry-After": "12"}
    # The provider's own words name the account; neither the answer nor the log repeats them.
    assert "gateway-key" not in json.dumps(result)
    assert "search.failed" in caplog.text
    assert "gateway-key" not in caplog.text


async def test_without_a_retry_after_there_is_no_header(provider: _Provider) -> None:
    result = await _search(provider)

    assert result["status"] == Status.EMBEDDING_RATE_LIMITED.value
    assert _failed_search_headers(result) is None


async def test_a_limit_that_lifts_during_the_retries_is_not_an_error(provider: _Provider) -> None:
    provider.refusals = retrieval._RETRIEVAL_EMBED_MAX_RETRIES - 1

    assert (await _search(provider))["status_code"] == 200


async def test_out_of_credit_is_still_its_own_answer(provider: _Provider) -> None:
    provider.error = {"message": "x", "type": "insufficient_quota", "code": "insufficient_quota"}

    assert (await _search(provider))["status"] == Status.EMBEDDING_OUT_OF_CREDIT.value


@pytest.mark.parametrize("status", [400, 401, 500])
async def test_a_failure_that_is_not_a_rate_limit_is_still_an_error(provider: _Provider, status: int) -> None:
    provider.status = status
    provider.error = {"message": "x", "type": "server_error", "code": None}

    result = await _search(provider)

    assert (result["status"], result["status_code"]) == (Status.ERROR.value, 500)


async def test_a_429_that_is_not_the_embedding_is_not_blamed_on_it() -> None:
    class _Refused(Exception):
        status_code = 429

    service = search_world._service(search_world._FakeStore())
    service.get_embedding_model_instance = AsyncMock(side_effect=_Refused())
    retrieval._user_cache.clear()

    result = await service.search_with_filters(["quarterly plan"], "alice", search_world.ORG, limit=5)

    assert result["status"] == Status.ERROR.value


def test_chat_treats_it_as_a_search_that_failed_not_one_that_found_nothing() -> None:
    assert Status.EMBEDDING_RATE_LIMITED.value in prefetch._SEARCH_FAILED_STATUSES


def test_the_gateway_repeats_the_message_word_for_word() -> None:
    gateway = Path(__file__).resolve().parents[6] / "backend/nodejs/apps/src/libs/errors/reader-friendly.ts"
    if not gateway.exists():
        pytest.skip("the Node sources are not in this checkout")
    assert retrieval.EMBEDDING_RATE_LIMITED_MESSAGE in gateway.read_text(encoding="utf-8")
