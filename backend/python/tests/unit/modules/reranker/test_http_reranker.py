"""``HttpReranker`` against every wire format: one contract, run per format."""

from __future__ import annotations

import json

import httpx
import pytest

from app.modules.reranker.http_reranker import HttpReranker
from app.modules.reranker.interface import IReranker, RerankerError, RerankHit
from app.modules.reranker.wire_formats import (
    CohereWireFormat,
    RerankWireFormat,
    VoyageWireFormat,
)

_DOCS = ["alpha", "beta", "gamma"]


def _reranker(handler, wire_format: RerankWireFormat, **kwargs) -> HttpReranker:
    return HttpReranker(
        url="https://rerank.example/v1/rerank",
        model="m",
        wire_format=wire_format,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


class _RerankerContract:
    """What every ``IReranker`` must do, whatever the provider speaks."""

    wire_format: RerankWireFormat
    results_key: str

    def _respond(self, scores: dict[int, float]):
        def handler(request: httpx.Request) -> httpx.Response:
            items = [{"index": i, "relevance_score": s} for i, s in scores.items()]
            return httpx.Response(200, json={self.results_key: items})

        return handler

    @pytest.mark.asyncio
    async def test_most_relevant_first(self) -> None:
        reranker = _reranker(self._respond({0: 0.1, 1: 0.9, 2: 0.5}), self.wire_format)
        assert await reranker.rerank("q", _DOCS) == [
            RerankHit(1, 0.9), RerankHit(2, 0.5), RerankHit(0, 0.1),
        ]

    @pytest.mark.asyncio
    async def test_top_n_limits_the_result(self) -> None:
        reranker = _reranker(self._respond({0: 0.1, 1: 0.9, 2: 0.5}), self.wire_format)
        assert [hit.index for hit in await reranker.rerank("q", _DOCS, top_n=2)] == [1, 2]

    @pytest.mark.asyncio
    async def test_no_documents_makes_no_call(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        assert await _reranker(handler, self.wire_format).rerank("q", []) == []

    @pytest.mark.asyncio
    async def test_indices_outside_the_input_are_ignored(self) -> None:
        reranker = _reranker(self._respond({0: 0.3, 7: 0.9}), self.wire_format)
        assert await reranker.rerank("q", _DOCS) == [RerankHit(0, 0.3)]

    @pytest.mark.asyncio
    async def test_http_error_becomes_reranker_error(self) -> None:
        reranker = _reranker(lambda r: httpx.Response(401, text="bad key"), self.wire_format)
        with pytest.raises(RerankerError, match="HTTP 401"):
            await reranker.rerank("q", _DOCS)

    @pytest.mark.asyncio
    async def test_transport_failure_becomes_reranker_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(RerankerError) as info:
            await _reranker(handler, self.wire_format).rerank("q", _DOCS)
        assert isinstance(info.value.__cause__, httpx.ConnectError)

    @pytest.mark.asyncio
    async def test_malformed_response_becomes_reranker_error(self) -> None:
        reranker = _reranker(lambda r: httpx.Response(200, json={"unexpected": []}), self.wire_format)
        with pytest.raises(RerankerError):
            await reranker.rerank("q", _DOCS)

    def test_is_an_ireranker(self) -> None:
        assert isinstance(_reranker(self._respond({}), self.wire_format), IReranker)


class TestCohereWireFormat(_RerankerContract):
    wire_format = CohereWireFormat()
    results_key = "results"

    @pytest.mark.asyncio
    async def test_request_shape_and_auth(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"results": []})

        await _reranker(handler, CohereWireFormat(), api_key="k").rerank("q", _DOCS, top_n=2)

        assert seen["body"] == {"model": "m", "query": "q", "documents": _DOCS, "top_n": 2}
        assert seen["auth"] == "Bearer k"

    @pytest.mark.asyncio
    async def test_extra_body_fields_are_sent(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"results": []})

        await _reranker(handler, CohereWireFormat(extra_body={"trust_remote_code": True})).rerank("q", _DOCS)

        assert seen["trust_remote_code"] is True
        assert "top_n" not in seen


class TestVoyageWireFormat(_RerankerContract):
    wire_format = VoyageWireFormat()
    results_key = "data"

    @pytest.mark.asyncio
    async def test_request_shape(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"data": []})

        await _reranker(handler, VoyageWireFormat()).rerank("q", _DOCS, top_n=1)

        assert seen == {"model": "m", "query": "q", "documents": _DOCS, "truncation": True, "top_k": 1}
