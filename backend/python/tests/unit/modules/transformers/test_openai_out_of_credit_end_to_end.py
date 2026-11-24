"""An OpenAI account with no credit, end to end: a local server answers every call the way api.openai.com does (a 429
whose code is ``insufficient_quota``), and the real SDK clients, built the way production builds them, are run through
indexing, search and the model check. Each one sends the paid request once and says "out of credit"."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.health import perform_llm_health_check
from app.exceptions.fastapi_responses import Status
from app.exceptions.indexing_exceptions import EmbeddingError
from app.modules.retrieval import retrieval_service as retrieval
from app.services.llm_gateway.embedding_gateway import EmbeddingGateway
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.spend_guard.retry_policy import RetryBudget
from app.utils import user_errors
from app.utils.aimodels import get_embedding_model
from tests.support.vectorstore_world import make_vectorstore
from tests.unit.modules.retrieval import test_source_diversity as search_world

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.usefixtures("bound_org")

_OPENAI_WORDS = (
    "You exceeded your current quota, please check your plan and billing details. For more information on this "
    "error, read the docs: https://platform.openai.com/docs/guides/error-codes/api-errors."
)


class _OutOfCreditOpenAI:
    """api.openai.com for an account whose balance is used up. Counts the requests it is sent."""

    def __init__(self, code: str) -> None:
        self.paths: list[str] = []
        world = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                world.paths.append(self.path)
                body = json.dumps(
                    {"error": {"message": _OPENAI_WORDS, "type": code, "param": None, "code": code}},
                ).encode()
                self.send_response(429)
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


# ``insufficient_quota`` is what OpenAI documents; ``credit_balance_exhausted`` is what the failing workspace received.
@pytest.fixture(params=["insufficient_quota", "credit_balance_exhausted"])
def openai_api(request: pytest.FixtureRequest) -> Iterator[_OutOfCreditOpenAI]:
    server = _OutOfCreditOpenAI(request.param)
    try:
        yield server
    finally:
        server.close()


def _embedder(server: _OutOfCreditOpenAI) -> Any:  # noqa: ANN401 - the provider's LangChain client
    config = {
        "provider": "openAI",
        "configuration": {"model": "text-embedding-3-small", "apiKey": "sk-test-out-of-credit", "endpoint": server.url},
    }
    return get_embedding_model("openAI", config, sdk_retries=0)


async def test_indexing_sends_the_batch_once_and_fails_the_record_as_out_of_credit(
    openai_api: _OutOfCreditOpenAI,
) -> None:
    store = make_vectorstore()
    store.embedding_provider = "openAI"
    store.dense_embeddings = _embedder(openai_api)
    gateway = EmbeddingGateway(retry_budget=RetryBudget())

    with patch("app.modules.transformers.vectorstore.get_embedding_gateway", return_value=gateway), \
            patch("app.modules.transformers.vectorstore.asyncio.sleep", AsyncMock()) as sleep, \
            pytest.raises(Exception) as raised:
        await store._embed_documents_with_retry(["hello world"], "rec-1")

    # One paid request, not three, and no backoff sleep between attempts that were never made.
    assert len(openai_api.paths) == 1
    sleep.assert_not_awaited()
    # The consumer does not re-queue the stage (it used to: TRANSIENT, three more requests per delivery).
    failure = EmbeddingError("Dense embedding failed")
    failure.__cause__ = raised.value
    assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL
    assert MessageErrorClassifier.classify_by_exception(failure) == MessageErrorType.TERMINAL
    # What the knowledge base shows next to the failed file.
    reason = user_errors.to_user_reason(failure)
    assert reason == user_errors._ai_messages("embedding model")["quota_exceeded"]
    assert "out of credit" in reason


async def test_search_embeds_the_query_once_and_answers_out_of_credit(openai_api: _OutOfCreditOpenAI) -> None:
    service = search_world._service(search_world._FakeStore())
    service.get_embedding_model_instance = AsyncMock(return_value=_embedder(openai_api))
    service._cached_embedding_is_local = False
    retrieval._user_cache.clear()

    with patch.object(retrieval, "describe_search_records", AsyncMock()), \
            patch.object(asyncio, "sleep", AsyncMock()) as sleep:
        result = await service.search_with_filters(["quarterly plan"], "alice", search_world.ORG, limit=5)

    assert len(openai_api.paths) == 1
    sleep.assert_not_awaited()
    assert result["status"] == Status.EMBEDDING_OUT_OF_CREDIT.value
    assert result["status_code"] == 503
    assert result["message"] == retrieval.EMBEDDING_OUT_OF_CREDIT_MESSAGE
    assert result["searchResults"] == []


async def test_saving_the_model_answers_400_out_of_credit_not_a_500_about_the_api_key(
    openai_api: _OutOfCreditOpenAI,
) -> None:
    config = {
        "provider": "openAI",
        "modelFriendlyName": "GPT",
        "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test-out-of-credit", "endpoint": openai_api.url},
    }

    response = await perform_llm_health_check(config, logging.getLogger("test"))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["details"]["error_code"] == "quota_exceeded"
    assert "out of credit" in body["message"]
    assert "Couldn't connect" not in body["message"]
    assert "sk-test-out-of-credit" not in body["message"]
