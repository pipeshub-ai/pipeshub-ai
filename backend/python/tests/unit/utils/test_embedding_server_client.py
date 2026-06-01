"""Unit tests for embedding server HTTP client retries."""

from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from app.utils.embedding_server_client import (
    EmbeddingServerEmbeddings,
    _is_retriable_embedding_error,
)


class TestRetriableErrors:
    def test_connection_error_is_retriable(self):
        assert _is_retriable_embedding_error(openai.APIConnectionError(request=MagicMock()))

    def test_timeout_is_retriable(self):
        assert _is_retriable_embedding_error(openai.APITimeoutError(request=MagicMock()))

    def test_503_is_retriable(self):
        response = MagicMock()
        response.status_code = 503
        assert _is_retriable_embedding_error(
            openai.APIStatusError("service unavailable", response=response, body=None)
        )

    def test_400_is_not_retriable(self):
        response = MagicMock()
        response.status_code = 400
        assert not _is_retriable_embedding_error(
            openai.BadRequestError("bad request", response=response, body=None)
        )


class TestEmbeddingServerEmbeddingsRetry:
    def test_call_with_retry_retries_transient_failure(self):
        calls = {"count": 0}

        def _fn():
            calls["count"] += 1
            if calls["count"] == 1:
                raise openai.APIConnectionError(request=MagicMock())
            return [0.1, 0.2, 0.3]

        from app.utils.embedding_server_client import _call_with_retry

        with patch("time.sleep", return_value=None):
            result = _call_with_retry(
                _fn,
                max_retries=3,
                operation="embed_query",
            )

        assert result == [0.1, 0.2, 0.3]
        assert calls["count"] == 2

    @pytest.mark.asyncio
    async def test_await_with_retry_retries_transient_failure(self):
        calls = {"count": 0}

        async def _fn():
            calls["count"] += 1
            if calls["count"] == 1:
                raise openai.APITimeoutError(request=MagicMock())
            return [0.4, 0.5, 0.6]

        from app.utils.embedding_server_client import _await_with_retry

        with patch(
            "app.utils.embedding_server_client.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await _await_with_retry(
                _fn,
                max_retries=3,
                operation="aembed_query",
            )

        assert result == [0.4, 0.5, 0.6]
        assert calls["count"] == 2

    def test_call_with_retry_does_not_retry_client_errors(self):
        def _fn():
            response = MagicMock()
            response.status_code = 400
            raise openai.BadRequestError(
                "bad request", response=response, body={"detail": "invalid"}
            )

        from app.utils.embedding_server_client import _call_with_retry

        with pytest.raises(openai.BadRequestError):
            _call_with_retry(_fn, max_retries=3, operation="embed_query")
