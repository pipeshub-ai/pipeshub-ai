"""``POST /health-check/reranker``: a reranker is healthy only if it ranks correctly."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.api.routes.health import (
    SUPPORTED_HEALTH_CHECK_TYPES,
    perform_reranker_health_check,
)
from app.modules.reranker.interface import IReranker, RerankerError, RerankHit

_CONFIG = {"provider": "cohere", "configuration": {"model": "rerank-v3.5", "apiKey": "k"}}


class _FakeReranker(IReranker):
    def __init__(self, *, hits=None, error: Exception | None = None) -> None:
        self._hits = hits or []
        self._error = error

    @property
    def model_name(self) -> str:
        return "rerank-v3.5"

    async def rerank(self, query, documents, top_n=None):
        if self._error:
            raise self._error
        return self._hits


async def _check(reranker: IReranker | Exception):
    factory = (
        MagicMock(side_effect=reranker) if isinstance(reranker, Exception)
        else MagicMock(return_value=reranker)
    )
    with patch("app.api.routes.health.create_reranker", factory):
        response = await perform_reranker_health_check(_CONFIG, MagicMock())
    return response.status_code, json.loads(response.body)


def test_reranker_is_a_supported_health_check_type() -> None:
    assert "reranker" in SUPPORTED_HEALTH_CHECK_TYPES


@pytest.mark.asyncio
async def test_healthy_when_the_relevant_passage_ranks_first() -> None:
    status, body = await _check(_FakeReranker(hits=[RerankHit(1, 0.97), RerankHit(0, 0.01)]))

    assert status == 200
    assert body["status"] == "healthy"
    assert body["details"]["model"] == "rerank-v3.5"
    assert body["details"]["latencyMs"] >= 0


@pytest.mark.asyncio
async def test_a_model_that_ranks_wrongly_is_rejected_as_a_config_error() -> None:
    status, body = await _check(_FakeReranker(hits=[RerankHit(0, 0.6), RerankHit(1, 0.4)]))

    assert status == 400
    assert "cross-encoder" in body["message"]


@pytest.mark.asyncio
async def test_incomplete_settings_are_a_config_error() -> None:
    status, body = await _check(ValueError("Reranker configuration has no model name"))

    assert status == 400
    assert body["message"] == "Reranker configuration has no model name"


@pytest.mark.asyncio
async def test_timeout_says_the_model_may_still_be_downloading() -> None:
    error = RerankerError("timed out")
    error.__cause__ = httpx.ReadTimeout("slow")

    status, body = await _check(_FakeReranker(error=error))

    assert status == 504
    assert body["details"]["error_code"] == "health_check_timeout"
    assert "downloading" in body["message"]


@pytest.mark.asyncio
async def test_provider_failure_is_reported_without_a_traceback() -> None:
    status, body = await _check(_FakeReranker(error=RerankerError("HTTP 401: invalid api key")))

    assert status == 500
    assert body["status"] == "error"
    assert "Traceback" not in body["message"]
