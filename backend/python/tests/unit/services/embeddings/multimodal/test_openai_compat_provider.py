from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embeddings.multimodal.openai_compat_provider import (
    OpenAICompatMultimodalProvider,
)


def _client(*responses):
    """An httpx.AsyncClient stub whose post() yields `responses` in order."""
    client = AsyncMock()
    if len(responses) == 1:
        client.post.return_value = responses[0]
    else:
        client.post.side_effect = list(responses)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _response(payload, *, raises=None):
    resp = MagicMock()
    resp.json.return_value = payload
    if raises is not None:
        resp.raise_for_status.side_effect = raises
    return resp


class TestOpenAICompatMultimodalProvider:
    def test_requires_base_url(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatMultimodalProvider(
                base_url=None, api_key="k", model_name="m",
            )

    def test_provider_name_is_the_label(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://e/v1", api_key=None, model_name="m", provider_label="lmStudio",
        )
        assert provider.provider_name == "lmStudio"

    @pytest.mark.asyncio
    async def test_success_uses_standard_input_format(self) -> None:
        """The standard OpenAI `input` schema is tried first (what routers such
        as Requesty/LiteLLM expect), not vLLM's `messages` extension."""
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1/",
            api_key="test-key",
            model_name="vertex/google/gemini-embedding-2-preview",
        )
        client = _client(_response({"data": [{"index": 0, "embedding": [0.1, 0.2]}]}))

        with patch("httpx.AsyncClient", return_value=client):
            results = await provider.embed_images(["aW1hZ2U="])

        assert [r.embedding for r in results] == [[0.1, 0.2]]
        client.post.assert_awaited_once()
        call = client.post.await_args
        assert call.args[0] == "http://embedding.test/v1/embeddings"
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert call.kwargs["json"]["input"] == ["data:image/jpeg;base64,aW1hZ2U="]
        assert "messages" not in call.kwargs["json"]

    @pytest.mark.asyncio
    async def test_existing_data_uri_is_passed_through_unchanged(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1", api_key=None, model_name="m",
        )
        client = _client(_response({"data": [{"index": 0, "embedding": [0.5]}]}))

        with patch("httpx.AsyncClient", return_value=client):
            await provider.embed_images(["data:image/png;base64,aW1hZ2U="])

        assert client.post.await_args.kwargs["json"]["input"] == [
            "data:image/png;base64,aW1hZ2U="
        ]

    @pytest.mark.asyncio
    async def test_falls_back_to_messages_when_input_format_rejected(self) -> None:
        """A self-hosted vLLM multimodal embedding server rejects `input` and
        only speaks the chat-`messages` extension."""
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1/",
            api_key="test-key",
            model_name="Qwen/Qwen3-VL-Embedding-2B",
        )
        client = _client(
            _response({}, raises=RuntimeError("400 Bad Request")),
            _response({"data": [{"index": 0, "embedding": [0.1, 0.2]}]}),
        )

        with patch("httpx.AsyncClient", return_value=client):
            results = await provider.embed_images(["aW1hZ2U="])

        assert [r.embedding for r in results] == [[0.1, 0.2]]
        assert client.post.await_count == 2
        first, second = client.post.await_args_list
        assert first.kwargs["json"]["input"] == ["data:image/jpeg;base64,aW1hZ2U="]
        assert "messages" not in first.kwargs["json"]
        content = second.kwargs["json"]["messages"][0]["content"]
        assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,aW1hZ2U="

    @pytest.mark.asyncio
    async def test_both_formats_failing_errors_every_index(self) -> None:
        logger = MagicMock()
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1", api_key=None, model_name="m", logger=logger,
        )
        client = _client(*[_response({}, raises=RuntimeError("boom"))] * 3)

        with patch("httpx.AsyncClient", return_value=client):
            results = await provider.embed_images(["aW1hZ2U=", "b3RoZXI="])

        assert [r.index for r in results] == [0, 1]
        assert all(r.embedding is None and r.error for r in results)
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_out_of_order_response_maps_by_index(self) -> None:
        """Regression: results were zipped to inputs by list position, so a
        server answering out of order attached each embedding to the wrong
        image. The response's own `index` decides."""
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1", api_key=None, model_name="m",
        )
        client = _client(_response({"data": [
            {"index": 1, "embedding": [1.0]},
            {"index": 0, "embedding": [0.0]},
        ]}))

        with patch("httpx.AsyncClient", return_value=client):
            results = await provider.embed_images(["aW1hZ2Uw", "aW1hZ2Ux"])

        assert [(r.index, r.embedding) for r in results] == [(0, [0.0]), (1, [1.0])]

    @pytest.mark.asyncio
    async def test_short_response_errors_the_missing_index(self) -> None:
        """Two images in, one embedding back — the unanswered index still owes
        the caller a result rather than vanishing."""
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1", api_key=None, model_name="m",
        )
        client = _client(_response({"data": [{"index": 0, "embedding": [0.1]}]}))

        with patch("httpx.AsyncClient", return_value=client):
            results = await provider.embed_images(["aW1hZ2Uw", "aW1hZ2Ux"])

        assert [r.index for r in results] == [0, 1]
        assert results[0].embedding == [0.1]
        assert results[1].embedding is None and results[1].error

    @pytest.mark.asyncio
    async def test_invalid_image_skips_the_request(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1", api_key=None, model_name="m",
        )
        client = _client(_response({"data": []}))

        with patch("httpx.AsyncClient", return_value=client):
            results = await provider.embed_images(["not valid base64!!"])

        assert results[0].error == "invalid image data"
        client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_indices_are_offset_correctly_across_batches(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://embedding.test/v1", api_key=None, model_name="m",
        )
        images = [f"aW1hZ2U{i}" for i in range(20)]

        def one_per_input(*_args, **kwargs):
            n = len(kwargs["json"]["input"])
            return _response({"data": [{"index": i, "embedding": [float(i)]} for i in range(n)]})

        client = AsyncMock()
        client.post.side_effect = one_per_input
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=client):
            results = await provider.embed_images(images)

        assert sorted(r.index for r in results) == list(range(20))
