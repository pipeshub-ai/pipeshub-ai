"""
Regression tests: Gemini rate limits and outages must be retried like OpenAI's.

The Gemini embedder (langchain_google_genai.GoogleGenerativeAIEmbeddings)
raises GoogleGenerativeAIError, chained from the google-genai APIError that
carries the HTTP code. The shared retry policy has to see through that wrapper,
or a Gemini 429 fails search and indexing on the first attempt.
"""

import asyncio

import pytest
from aiohttp import web
from google.genai import errors as genai_errors
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_google_genai._common import GoogleGenerativeAIError

from app.utils import embedding_retry
from app.utils.embedding_retry import await_with_retry, is_retriable_embedding_error


def _google_error(code: int, status: str) -> genai_errors.APIError:
    body = {"error": {"code": code, "message": f"Simulated {status}.", "status": status}}
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, body)


def _wrapped_like_langchain(code: int, status: str) -> GoogleGenerativeAIError:
    """Raise and catch the error the way langchain_google_genai does."""
    try:
        try:
            raise _google_error(code, status)
        except genai_errors.APIError as e:
            raise GoogleGenerativeAIError(f"Error embedding content ({status}): {e}") from e
    except GoogleGenerativeAIError as wrapped:
        return wrapped


class TestGeminiErrorClassification:
    @pytest.mark.parametrize(
        ("code", "status"),
        [(429, "RESOURCE_EXHAUSTED"), (502, "UNAVAILABLE"), (503, "UNAVAILABLE"), (504, "DEADLINE_EXCEEDED")],
    )
    def test_transient_google_errors_are_retriable(self, code: int, status: str) -> None:
        assert is_retriable_embedding_error(_wrapped_like_langchain(code, status))
        assert is_retriable_embedding_error(_google_error(code, status))

    @pytest.mark.parametrize(
        ("code", "status"),
        [(400, "INVALID_ARGUMENT"), (403, "PERMISSION_DENIED"), (404, "NOT_FOUND"), (500, "INTERNAL")],
    )
    def test_other_google_errors_are_not_retriable(self, code: int, status: str) -> None:
        assert not is_retriable_embedding_error(_wrapped_like_langchain(code, status))
        assert not is_retriable_embedding_error(_google_error(code, status))

    def test_wrapper_without_google_cause_is_not_retriable(self) -> None:
        assert not is_retriable_embedding_error(GoogleGenerativeAIError("Error embedding content: bad input"))


class _FakeGemini:
    """Answers every embed request with 429 a set number of times, then 200."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.requests = 0

    async def handle(self, request: web.Request) -> web.Response:
        self.requests += 1
        if self.requests <= self.failures:
            return web.json_response(
                {"error": {"code": 429, "message": "Resource exhausted. Please try again later.",
                           "status": "RESOURCE_EXHAUSTED"}},
                status=429,
            )
        return web.json_response({"embeddings": [{"values": [0.1, 0.2, 0.3]}]})


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_retry, "retry_delay_seconds", lambda attempt: 0.0)


async def _serve(fake: _FakeGemini) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


@pytest.mark.usefixtures("no_backoff")
async def test_query_embedding_recovers_from_a_gemini_429() -> None:
    """The search path: one throttled query embedding must not fail the search."""
    fake = _FakeGemini(failures=1)
    runner, base_url = await _serve(fake)
    try:
        embedder = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-2", google_api_key="test-key", base_url=base_url
        )
        vector = await asyncio.wait_for(
            await_with_retry(
                lambda: embedder.aembed_query("vpn error 809"),
                max_retries=3,
                operation="aembed_query",
                service_name="retrieval",
            ),
            timeout=10,
        )
        assert vector == [0.1, 0.2, 0.3]
        assert fake.requests == 2
    finally:
        await runner.cleanup()


@pytest.mark.usefixtures("no_backoff")
async def test_persistent_gemini_429_still_fails_after_the_retry_budget() -> None:
    fake = _FakeGemini(failures=10)
    runner, base_url = await _serve(fake)
    try:
        embedder = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-2", google_api_key="test-key", base_url=base_url
        )
        with pytest.raises(GoogleGenerativeAIError):
            await asyncio.wait_for(
                await_with_retry(
                    lambda: embedder.aembed_query("vpn error 809"),
                    max_retries=3,
                    operation="aembed_query",
                    service_name="retrieval",
                ),
                timeout=10,
            )
        assert fake.requests == 3
    finally:
        await runner.cleanup()
