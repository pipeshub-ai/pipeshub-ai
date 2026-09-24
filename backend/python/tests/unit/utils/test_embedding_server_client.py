"""Unit tests for the shared embedding retry policy and the embedding server client."""

from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_SERVER_URL,
    EMBEDDING_SERVER_MAX_RETRIES,
    EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS,
)
from app.services.messaging.backpressure import BackpressureCoordinator
from app.utils.embedding_retry import (
    await_with_retry,
    call_with_retry,
    is_retriable_embedding_error,
    retry_delay_seconds,
    signal_backpressure_if_rate_limited,
)
from app.utils.embedding_server_client import (
    EmbeddingServerEmbeddings,
    _embedding_server_base_url,
    _embedding_server_max_retries,
    _embedding_server_timeout,
    get_embedding_server_embeddings,
)


def _rate_limit_error(retry_after: str | None) -> openai.RateLimitError:
    response = MagicMock()
    response.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return openai.RateLimitError("rate limited", response=response, body=None)


class TestRetriableErrors:
    def test_connection_error_is_retriable(self):
        assert is_retriable_embedding_error(openai.APIConnectionError(request=MagicMock()))

    def test_timeout_is_retriable(self):
        assert is_retriable_embedding_error(openai.APITimeoutError(request=MagicMock()))

    def test_503_is_retriable(self):
        response = MagicMock()
        response.status_code = 503
        assert is_retriable_embedding_error(
            openai.APIStatusError("service unavailable", response=response, body=None)
        )

    def test_400_is_not_retriable(self):
        response = MagicMock()
        response.status_code = 400
        assert not is_retriable_embedding_error(
            openai.BadRequestError("bad request", response=response, body=None)
        )

    def test_500_is_not_retriable(self):
        response = MagicMock()
        response.status_code = 500
        assert not is_retriable_embedding_error(
            openai.InternalServerError(
                "Failed to generate embeddings: trust_remote_code required",
                response=response,
                body={"detail": "trust_remote_code=True required"},
            )
        )

    def test_502_is_retriable(self):
        response = MagicMock()
        response.status_code = 502
        assert is_retriable_embedding_error(
            openai.APIStatusError("bad gateway", response=response, body=None)
        )


class TestEmbeddingServerEmbeddingsRetry:
    def test_call_with_retry_retries_transient_failure(self):
        calls = {"count": 0}

        def _fn():
            calls["count"] += 1
            if calls["count"] == 1:
                raise openai.APIConnectionError(request=MagicMock())
            return [0.1, 0.2, 0.3]


        with patch("time.sleep", return_value=None):
            result = call_with_retry(
                _fn,
                max_retries=3,
                operation="embed_query",
                service_name="EmbeddingServer",
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


        with patch(
            "app.utils.embedding_retry.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await await_with_retry(
                _fn,
                max_retries=3,
                operation="aembed_query",
                service_name="EmbeddingServer",
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


        with pytest.raises(openai.BadRequestError):
            call_with_retry(_fn, max_retries=3, operation="embed_query", service_name="EmbeddingServer")

    def test_call_with_retry_does_not_retry_500_application_errors(self):
        calls = {"count": 0}

        def _fn():
            calls["count"] += 1
            response = MagicMock()
            response.status_code = 500
            raise openai.InternalServerError(
                "Error code: 500 - trust_remote_code=True required",
                response=response,
                body={"detail": "trust_remote_code=True required"},
            )


        with pytest.raises(openai.InternalServerError):
            call_with_retry(_fn, max_retries=5, operation="embed_documents", service_name="EmbeddingServer")

        assert calls["count"] == 1


class TestEmbeddingServerConfig:
    def test_base_url_appends_v1_to_default(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_SERVER_URL", raising=False)
        assert _embedding_server_base_url() == f"{DEFAULT_EMBEDDING_SERVER_URL}/v1"

    def test_base_url_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_URL", "http://embed:8002/")
        assert _embedding_server_base_url() == "http://embed:8002/v1"

    def test_base_url_preserves_existing_v1_suffix(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_URL", "http://embed:8002/v1")
        assert _embedding_server_base_url() == "http://embed:8002/v1"

    def test_max_retries_default(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_SERVER_MAX_RETRIES", raising=False)
        assert _embedding_server_max_retries() == EMBEDDING_SERVER_MAX_RETRIES

    def test_max_retries_from_env(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_MAX_RETRIES", "7")
        assert _embedding_server_max_retries() == 7

    def test_max_retries_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_MAX_RETRIES", "not-a-number")
        assert _embedding_server_max_retries() == EMBEDDING_SERVER_MAX_RETRIES

    def test_max_retries_clamps_to_minimum_one(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_MAX_RETRIES", "0")
        assert _embedding_server_max_retries() == 1

    def test_timeout_default(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_SERVER_TIMEOUT", raising=False)
        assert _embedding_server_timeout() == EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS

    def test_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_TIMEOUT", "45.5")
        assert _embedding_server_timeout() == 45.5

    def test_timeout_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_TIMEOUT", "bad")
        assert _embedding_server_timeout() == EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS

    def test_timeout_clamps_to_minimum_one(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_SERVER_TIMEOUT", "0.1")
        assert _embedding_server_timeout() == 1.0


class TestRetryDelaySeconds:
    def test_exponential_backoff(self):
        assert retry_delay_seconds(1) == 2.0
        assert retry_delay_seconds(2) == 4.0
        assert retry_delay_seconds(3) == 8.0

    def test_backoff_capped_at_thirty_seconds(self):
        assert retry_delay_seconds(10) == 30.0


class TestRetriableErrorsExtended:
    def test_rate_limit_is_retriable(self):
        assert is_retriable_embedding_error(openai.RateLimitError(
            "rate limited", response=MagicMock(), body=None
        ))

    def test_429_is_retriable(self):
        response = MagicMock()
        response.status_code = 429
        assert is_retriable_embedding_error(
            openai.APIStatusError("too many requests", response=response, body=None)
        )

    def test_504_is_retriable(self):
        response = MagicMock()
        response.status_code = 504
        assert is_retriable_embedding_error(
            openai.APIStatusError("gateway timeout", response=response, body=None)
        )

    def test_non_openai_exception_is_not_retriable(self):
        assert not is_retriable_embedding_error(RuntimeError("boom"))

    def test_api_status_error_with_non_retriable_code(self):
        response = MagicMock()
        response.status_code = 404
        assert not is_retriable_embedding_error(
            openai.APIStatusError("not found", response=response, body=None)
        )


class TestRetryExhaustion:
    def test_call_with_retry_raises_after_all_attempts_exhausted(self):
        calls = {"count": 0}

        def _fn():
            calls["count"] += 1
            raise openai.APIConnectionError(request=MagicMock())


        with patch("time.sleep", return_value=None):
            with pytest.raises(openai.APIConnectionError):
                call_with_retry(_fn, max_retries=2, operation="embed_query", service_name="EmbeddingServer")

        assert calls["count"] == 2

    def test_call_with_retry_runtime_error_when_loop_skipped(self):

        with patch("app.utils.embedding_retry.range", return_value=iter([])):
            with pytest.raises(RuntimeError, match="failed without exception"):
                call_with_retry(lambda: None, max_retries=3, operation="embed_query", service_name="EmbeddingServer")

    @pytest.mark.asyncio
    async def test_await_with_retry_raises_after_all_attempts_exhausted(self):
        calls = {"count": 0}

        async def _fn():
            calls["count"] += 1
            raise openai.RateLimitError(
                "rate limited", response=MagicMock(), body=None
            )


        with patch(
            "app.utils.embedding_retry.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            with pytest.raises(openai.RateLimitError):
                await await_with_retry(
                    _fn, max_retries=2, operation="aembed_query",
                    service_name="EmbeddingServer",
                )

        assert calls["count"] == 2

    @pytest.mark.asyncio
    async def test_await_with_retry_runtime_error_when_loop_skipped(self):

        with patch("app.utils.embedding_retry.range", return_value=iter([])):
            with pytest.raises(RuntimeError, match="failed without exception"):
                await await_with_retry(
                    AsyncMock(return_value=None),
                    max_retries=3,
                    operation="aembed_query",
                    service_name="EmbeddingServer",
                )


class TestSignalBackpressureIfRateLimited:
    def test_signals_coordinator_on_429_with_retry_after(self):
        coordinator = BackpressureCoordinator()
        exc = _rate_limit_error("12")

        signal_backpressure_if_rate_limited(exc, service_name="EmbeddingServer", coordinator=coordinator)

        assert coordinator.is_paused() is True
        assert coordinator.paused_services == frozenset({"EmbeddingServer"})

    def test_no_coordinator_is_a_noop(self):
        exc = _rate_limit_error("12")
        signal_backpressure_if_rate_limited(exc, service_name="EmbeddingServer", coordinator=None)  # must not raise

    def test_non_rate_limit_error_does_not_signal(self):
        coordinator = BackpressureCoordinator()
        response = MagicMock()
        response.status_code = 503
        exc = openai.APIStatusError("service unavailable", response=response, body=None)

        signal_backpressure_if_rate_limited(exc, service_name="EmbeddingServer", coordinator=coordinator)

        assert coordinator.is_paused() is False

    def test_rate_limit_without_retry_after_does_not_signal(self):
        coordinator = BackpressureCoordinator()
        exc = _rate_limit_error(None)

        signal_backpressure_if_rate_limited(exc, service_name="EmbeddingServer", coordinator=coordinator)

        assert coordinator.is_paused() is False


class TestCallWithRetrySignalsBackpressure:
    def test_call_with_retry_signals_on_every_429_before_retrying(self):
        coordinator = BackpressureCoordinator()
        calls = {"count": 0}

        def _fn():
            calls["count"] += 1
            if calls["count"] == 1:
                raise _rate_limit_error("9")
            return [0.1]


        with patch("time.sleep", return_value=None):
            result = call_with_retry(
                _fn, max_retries=3, operation="embed_query", service_name="EmbeddingServer",
                backpressure_coordinator=coordinator,
            )

        assert result == [0.1]
        assert coordinator.paused_services == frozenset({"EmbeddingServer"})

    @pytest.mark.asyncio
    async def test_await_with_retry_signals_on_every_429_before_retrying(self):
        coordinator = BackpressureCoordinator()
        calls = {"count": 0}

        async def _fn():
            calls["count"] += 1
            if calls["count"] == 1:
                raise _rate_limit_error("9")
            return [0.2]


        with patch(
            "app.utils.embedding_retry.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await await_with_retry(
                _fn, max_retries=3, operation="aembed_query", service_name="EmbeddingServer",
                backpressure_coordinator=coordinator,
            )

        assert result == [0.2]
        assert coordinator.paused_services == frozenset({"EmbeddingServer"})


class TestEmbeddingServerEmbeddingsBackpressure:
    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    def test_uses_default_coordinator_when_none_passed(self, mock_openai_cls):
        from app.services.messaging.backpressure import (
            get_default_backpressure_coordinator,
        )

        client = EmbeddingServerEmbeddings(max_retries=1)

        assert client._backpressure_coordinator is get_default_backpressure_coordinator()

    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    def test_explicit_coordinator_overrides_default(self, mock_openai_cls):
        coordinator = BackpressureCoordinator()
        client = EmbeddingServerEmbeddings(max_retries=1, backpressure_coordinator=coordinator)

        assert client._backpressure_coordinator is coordinator

    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    def test_embed_query_signals_coordinator_on_rate_limit(self, mock_openai_cls):
        coordinator = BackpressureCoordinator()
        mock_inner = MagicMock()
        mock_inner.embed_query.side_effect = [_rate_limit_error("6"), [0.9]]
        mock_openai_cls.return_value = mock_inner

        client = EmbeddingServerEmbeddings(max_retries=2, backpressure_coordinator=coordinator)

        with patch("time.sleep", return_value=None):
            result = client.embed_query("hello")

        assert result == [0.9]
        assert coordinator.paused_services == frozenset({"EmbeddingServer"})


class TestEmbeddingServerEmbeddings:
    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    def test_init_uses_defaults(self, mock_openai_cls, monkeypatch):
        monkeypatch.delenv("EMBEDDING_SERVER_MAX_RETRIES", raising=False)
        monkeypatch.delenv("EMBEDDING_SERVER_TIMEOUT", raising=False)

        client = EmbeddingServerEmbeddings()

        assert client.model == DEFAULT_EMBEDDING_MODEL
        assert client.max_retries == EMBEDDING_SERVER_MAX_RETRIES
        assert client.timeout == EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS
        assert client.trust_remote_code is False
        mock_openai_cls.assert_called_once_with(
            model=DEFAULT_EMBEDDING_MODEL,
            api_key="not-needed",
            base_url=_embedding_server_base_url(),
            check_embedding_ctx_length=False,
            max_retries=0,
            timeout=EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS,
            extra_body=None,
        )

    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    def test_init_with_explicit_params_and_trust_remote_code(self, mock_openai_cls):
        client = EmbeddingServerEmbeddings(
            model="custom-model",
            max_retries=4,
            timeout=120.0,
            trust_remote_code=True,
        )

        assert client.model == "custom-model"
        assert client.max_retries == 4
        assert client.timeout == 120.0
        assert client.trust_remote_code is True
        mock_openai_cls.assert_called_once_with(
            model="custom-model",
            api_key="not-needed",
            base_url=_embedding_server_base_url(),
            check_embedding_ctx_length=False,
            max_retries=0,
            timeout=120.0,
            extra_body={"trust_remote_code": True},
        )

    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    def test_embed_documents_delegates_to_inner(self, mock_openai_cls):
        mock_inner = MagicMock()
        mock_inner.embed_documents.return_value = [[0.1, 0.2], [0.3, 0.4]]
        mock_openai_cls.return_value = mock_inner

        client = EmbeddingServerEmbeddings(max_retries=1)
        result = client.embed_documents(["hello", "world"])

        assert result == [[0.1, 0.2], [0.3, 0.4]]
        mock_inner.embed_documents.assert_called_once_with(["hello", "world"])

    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    def test_embed_query_delegates_to_inner(self, mock_openai_cls):
        mock_inner = MagicMock()
        mock_inner.embed_query.return_value = [0.5, 0.6]
        mock_openai_cls.return_value = mock_inner

        client = EmbeddingServerEmbeddings(max_retries=1)
        result = client.embed_query("query text")

        assert result == [0.5, 0.6]
        mock_inner.embed_query.assert_called_once_with("query text")

    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    @pytest.mark.asyncio
    async def test_aembed_documents_delegates_to_inner(self, mock_openai_cls):
        mock_inner = MagicMock()
        mock_inner.aembed_documents = AsyncMock(return_value=[[0.7], [0.8]])
        mock_openai_cls.return_value = mock_inner

        client = EmbeddingServerEmbeddings(max_retries=1)
        result = await client.aembed_documents(["async", "docs"])

        assert result == [[0.7], [0.8]]
        mock_inner.aembed_documents.assert_awaited_once_with(["async", "docs"])

    @patch("app.utils.embedding_server_client.OpenAIEmbeddings")
    @pytest.mark.asyncio
    async def test_aembed_query_delegates_to_inner(self, mock_openai_cls):
        mock_inner = MagicMock()
        mock_inner.aembed_query = AsyncMock(return_value=[0.9, 1.0])
        mock_openai_cls.return_value = mock_inner

        client = EmbeddingServerEmbeddings(max_retries=1)
        result = await client.aembed_query("async query")

        assert result == [0.9, 1.0]
        mock_inner.aembed_query.assert_awaited_once_with("async query")


class TestGetEmbeddingServerEmbeddings:
    @patch("app.utils.embedding_server_client.EmbeddingServerEmbeddings")
    def test_factory_returns_configured_client(self, mock_cls):
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        result = get_embedding_server_embeddings(
            "my-model",
            trust_remote_code=True,
        )

        mock_cls.assert_called_once_with(
            model="my-model",
            trust_remote_code=True,
        )
        assert result is mock_instance
