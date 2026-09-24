"""Capability probes in the model health check.

A health check may only claim what it verified. These cover the three things
the check used to assume: that a model accepts bound tools (every agent turn
binds them), that an embedding model flagged multimodal can actually embed an
image (that flag is what routes images into the image pipeline at all), and
that a failed image probe means what the message says it means.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi.responses import JSONResponse

MODULE = "app.api.routes.health"
# The factory is imported lazily inside the probe, so it is patched at its
# source rather than on the route module.
FACTORY = "app.services.embeddings.multimodal.factory.MultimodalEmbeddingFactory"


@pytest.fixture
def mock_request():
    """Same shape as the fixture in `test_health.py` — a request whose
    container yields a logger and a retrieval service."""
    request = MagicMock()
    container = MagicMock()
    container.logger.return_value = MagicMock()
    container.config_service.return_value = MagicMock()

    retrieval = AsyncMock()
    retrieval.collection_name = "test_collection"
    retrieval.vector_db_service = AsyncMock()
    retrieval.get_current_embedding_model_name = AsyncMock(return_value="model-a")
    retrieval.get_embedding_model_name = MagicMock(return_value="model-a")
    container.retrieval_service = AsyncMock(return_value=retrieval)

    request.app = MagicMock(container=container)
    return request


def _llm_config(**overrides) -> dict:
    config = {"provider": "openai", "configuration": {"model": "gpt-4o"}}
    config.update(overrides)
    return config


def _embedding_config(provider: str = "cohere", **overrides) -> dict:
    config = {
        "provider": provider,
        "configuration": {"model": "embed-v4", "apiKey": "sk-test"},
    }
    config.update(overrides)
    return config


def _error(message: str, status: int | None = None) -> Exception:
    exc = Exception(message)
    if status is not None:
        exc.status_code = status
    return exc


class TestToolCallingProbe:
    """Every agent turn binds tools and `_bind_tools` fails the turn rather
    than dropping them, so a model that cannot take tools is unusable for
    agents even though it answers plain prompts."""

    async def test_a_model_that_binds_tools_reports_the_capability(self) -> None:
        model = MagicMock()
        model.bind_tools.return_value = model

        with patch(f"{MODULE}.get_generator_model", return_value=model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(_llm_config(isMultimodal=False), MagicMock())

        assert resp.status_code == 200
        assert '"tool_calling":true' in resp.body.decode().replace(" ", "")

    async def test_a_model_that_cannot_bind_tools_is_healthy_but_flagged(self) -> None:
        """Still valid for indexing and image description, so this is reported
        rather than fatal — but an admin picking it for agents must be told."""
        model = MagicMock()
        model.bind_tools.side_effect = NotImplementedError("no tool support")

        with patch(f"{MODULE}.get_generator_model", return_value=model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(_llm_config(isMultimodal=False), MagicMock())

        body = resp.body.decode()
        assert resp.status_code == 200
        assert '"tool_calling":false' in body.replace(" ", "")
        assert "cannot be used for agents" in body

    async def test_a_provider_that_rejects_a_request_carrying_tools(self) -> None:
        model = MagicMock()
        model.bind_tools.return_value = model
        calls = 0

        async def wait_for(awaitable, timeout=None):
            nonlocal calls
            calls += 1
            if calls > 1:      # the tool-bound call
                raise _error("Tools are not supported for this model", 400)
            return "ok"

        with patch(f"{MODULE}.get_generator_model", return_value=model), \
             patch("asyncio.wait_for", side_effect=wait_for):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(_llm_config(isMultimodal=False), MagicMock())

        assert resp.status_code == 200
        assert '"tool_calling":false' in resp.body.decode().replace(" ", "")

    async def test_a_rate_limit_on_the_tool_probe_is_inconclusive_not_negative(self) -> None:
        """The text probe already proved the model answers; a 429 on the tool
        call says nothing about tool support, so we must not report false."""
        model = MagicMock()
        model.bind_tools.return_value = model
        calls = 0

        async def wait_for(awaitable, timeout=None):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise _error("Rate limit reached", 429)
            return "ok"

        with patch(f"{MODULE}.get_generator_model", return_value=model), \
             patch("asyncio.wait_for", side_effect=wait_for):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(_llm_config(isMultimodal=False), MagicMock())

        assert '"tool_calling":true' in resp.body.decode().replace(" ", "")


class TestVisionVerdict:
    """"This model has no vision" is a verdict that tells an admin to disable a
    feature, so it may only be reached from an error that actually says so."""

    @staticmethod
    async def _run(image_error: Exception):
        model = MagicMock()
        model.bind_tools.return_value = model
        calls = 0

        async def wait_for(awaitable, timeout=None):
            nonlocal calls
            calls += 1
            if calls == 2:     # text probe, then image probe
                raise image_error
            return "ok"

        with patch(f"{MODULE}.get_generator_model", return_value=model), \
             patch("asyncio.wait_for", side_effect=wait_for):
            from app.api.routes.health import perform_llm_health_check
            return await perform_llm_health_check(_llm_config(isMultimodal=True), MagicMock())

    @pytest.mark.parametrize(
        "message",
        [
            "This model does not support image input",
            "Invalid content type: image_url is unsupported",
            "vision is not enabled for this deployment",
        ],
    )
    async def test_capability_errors_produce_the_vision_verdict(self, message: str) -> None:
        resp = await self._run(_error(message, 400))
        assert resp.status_code == 400
        assert "doesn't support images" in resp.body.decode()

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 401, 403])
    async def test_transient_and_auth_errors_do_not(self, status: int) -> None:
        resp = await self._run(_error("upstream hiccup", status))
        assert "doesn't support images" not in resp.body.decode()

    async def test_a_timeout_is_reported_as_a_timeout(self) -> None:
        resp = await self._run(asyncio.TimeoutError())
        assert resp.status_code == 504
        assert "timed out" in resp.body.decode()

    async def test_an_empty_answer_to_the_image_is_not_a_pass(self) -> None:
        """A model that ignored the image and returned nothing must not be
        reported as vision-capable."""
        model = MagicMock()
        model.bind_tools.return_value = model
        calls = 0

        async def wait_for(awaitable, timeout=None):
            nonlocal calls
            calls += 1
            return "ok" if calls == 1 else "   "

        with patch(f"{MODULE}.get_generator_model", return_value=model), \
             patch("asyncio.wait_for", side_effect=wait_for):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(_llm_config(isMultimodal=True), MagicMock())

        assert resp.status_code == 400
        assert "empty response" in resp.body.decode()

    async def test_the_image_probe_carries_a_question(self) -> None:
        """A bare image block lets a model that ignored it still answer, and
        some gateways reject an image-only user turn outright."""
        model = MagicMock()
        model.bind_tools.return_value = model
        seen: list = []

        async def wait_for(awaitable, timeout=None):
            return "ok"

        def capture(payload):
            seen.append(payload)
            return MagicMock(content="a blue square")

        model.ainvoke = AsyncMock(side_effect=lambda payload: capture(payload))

        with patch(f"{MODULE}.get_generator_model", return_value=model):
            from app.api.routes.health import perform_llm_health_check
            await perform_llm_health_check(_llm_config(isMultimodal=True), MagicMock())

        image_payloads = [
            p for p in seen
            if isinstance(p, list) and isinstance(getattr(p[0], "content", None), list)
        ]
        assert image_payloads, "no multimodal message was sent"
        blocks = image_payloads[0][0].content
        assert any(b.get("type") == "text" for b in blocks)
        assert any(b.get("type") == "image_url" for b in blocks)


class TestImageEmbeddingProbe:
    """`isMultimodal` on an embedding model is what makes indexing send images
    down the image-embedding path; only a handful of providers implement one."""

    @staticmethod
    def _patch_text_embedding(dimension: int = 1024):
        model = MagicMock()
        model.embed_documents.return_value = [[0.1] * dimension]
        return patch(f"{MODULE}.get_embedding_model", return_value=model)

    async def test_a_provider_without_image_support_fails_the_check(self, mock_request) -> None:
        """Otherwise images are silently never indexed — the runtime only logs
        a warning and returns no points."""
        with self._patch_text_embedding(), \
             patch(FACTORY) as factory:
            factory.create.return_value = None
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(
                mock_request, _embedding_config("openai", isMultimodal=True), MagicMock(),
            )

        body = resp.body.decode()
        assert resp.status_code == 400
        assert "no image-embedding support" in body

    async def test_a_working_multimodal_model_passes(self, mock_request) -> None:
        provider = MagicMock()
        provider.supports_multimodal.return_value = True
        provider.embed_images = AsyncMock(return_value=[MagicMock(embedding=[0.2] * 1024)])

        with self._patch_text_embedding(), \
             patch(FACTORY) as factory:
            factory.create.return_value = provider
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(
                mock_request, _embedding_config(isMultimodal=True), MagicMock(),
            )

        assert resp.status_code == 200
        assert '"multimodal":true' in resp.body.decode().replace(" ", "")

    async def test_image_and_text_vectors_must_share_a_width(self, mock_request) -> None:
        """Both land in one collection, which holds a single vector width."""
        provider = MagicMock()
        provider.supports_multimodal.return_value = True
        provider.embed_images = AsyncMock(return_value=[MagicMock(embedding=[0.2] * 512)])

        with self._patch_text_embedding(1024), \
             patch(FACTORY) as factory:
            factory.create.return_value = provider
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(
                mock_request, _embedding_config(isMultimodal=True), MagicMock(),
            )

        assert resp.status_code == 400
        assert "512" in resp.body.decode()

    async def test_a_text_only_model_is_not_probed_for_images(self, mock_request) -> None:
        with self._patch_text_embedding(), \
             patch(FACTORY) as factory:
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(
                mock_request, _embedding_config("openai"), MagicMock(),
            )

        assert resp.status_code == 200
        factory.create.assert_not_called()


class TestEmbeddingDimensionChecks:
    async def test_ragged_vectors_are_rejected(self, mock_request) -> None:
        """The old code computed this comparison and threw the result away."""
        model = MagicMock()
        model.embed_documents.return_value = [[0.1] * 1024, [0.1] * 512]

        with patch(f"{MODULE}.get_embedding_model", return_value=model), \
             patch(f"{MODULE}._embed_with_timeout", new_callable=AsyncMock,
                   return_value=[[0.1] * 1024, [0.1] * 512]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(
                mock_request, _embedding_config("openai"), MagicMock(),
            )

        assert resp.status_code == 400
        assert "differing sizes" in resp.body.decode()

    async def test_an_ignored_dimensions_override_is_caught(self, mock_request) -> None:
        """A provider that ignores `dimensions` would otherwise build a
        collection of the wrong width, found only when queries return nothing."""
        model = MagicMock()
        config = _embedding_config("openai")
        config["configuration"]["dimensions"] = 256

        with patch(f"{MODULE}.get_embedding_model", return_value=model), \
             patch(f"{MODULE}._embed_with_timeout", new_callable=AsyncMock,
                   return_value=[[0.1] * 1024]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, MagicMock())

        body = resp.body.decode()
        assert resp.status_code == 400
        assert "asked for 256" in body


class TestConfigurationWarnings:
    """A setting that is wrong but no longer fatal still has to be reported —
    silently correcting it leaves the config wrong for the next reader."""

    NOVA = (
        "arn:aws:bedrock:ap-south-1:108782071197:inference-profile/"
        "global.amazon.nova-2-lite-v1:0"
    )

    async def _run(self, inner_provider: str, model: str) -> str:
        config = {
            "provider": "bedrock",
            "isReasoning": True,
            "configuration": {"model": model, "provider": inner_provider, "region": "ap-south-1"},
        }
        resp = await _run_llm_check(config)
        assert resp.status_code == 200
        return resp.body.decode()

    async def test_a_mismatched_bedrock_provider_is_reported(self) -> None:
        """Previously this surfaced as Bedrock's own 'extraneous key
        [thinking] is not permitted', which named neither the setting nor the
        model."""
        body = await self._run("anthropic", self.NOVA)

        assert "provider is set to 'anthropic'" in body
        assert "identifies it as 'amazon'" in body
        assert "update the provider" in body

    async def test_a_correct_configuration_says_nothing(self) -> None:
        body = await self._run("amazon", self.NOVA)
        assert "update the provider" not in body

    async def test_a_non_bedrock_provider_is_not_inspected(self) -> None:
        resp = await _run_llm_check(_llm_config())

        assert "update the provider" not in resp.body.decode()


def _model(*, stream_chunks: list[str] | None = None, stream_error: Exception | None = None) -> MagicMock:
    """A chat model that answers, binds tools, and streams — the shape
    `_check_one_llm` probes. `ainvoke`/`astream` are real coroutine functions
    because the probes dispatch on `inspect.iscoroutinefunction`."""
    model = MagicMock()
    model.bind_tools.return_value = model

    async def ainvoke(_payload: object) -> MagicMock:
        return MagicMock(content="ok")

    async def astream(_payload: object) -> AsyncIterator[MagicMock]:
        if stream_error is not None:
            raise stream_error
        for chunk in ("hel", "lo") if stream_chunks is None else stream_chunks:
            yield MagicMock(content=chunk)

    model.ainvoke = ainvoke
    model.astream = astream
    return model


async def _run_llm_check(config: dict, model: MagicMock | None = None) -> JSONResponse:
    from app.api.routes.health import perform_llm_health_check

    with patch(f"{MODULE}.get_generator_model", return_value=model or _model()):
        return await perform_llm_health_check(config, MagicMock())


def _body(response) -> dict:
    import json

    return json.loads(response.body.decode())


class TestContextLength:
    """The configured window decides how much of a document one read returns
    (`resolve_render_budget`) and which prompt scaffolding the model gets.
    Left unset it falls back to a default, which is how a 1M-token model ends
    up behaving like a 128k one — invisible at runtime, so it is said here."""

    async def test_an_unset_window_is_reported_not_assumed_silently(self) -> None:
        response = await _run_llm_check(_llm_config())

        assert response.status_code == 200
        body = _body(response)
        assert "context length is not set" in body["message"]
        from app.api.routes.health import ASSUMED_CONTEXT_LENGTH
        assert body["capabilities"]["context_length"] == ASSUMED_CONTEXT_LENGTH

    async def test_a_configured_window_is_reported_as_configured(self) -> None:
        body = _body(await _run_llm_check(_llm_config(contextLength=1_000_000)))

        assert body["capabilities"]["context_length"] == 1_000_000
        assert "context length is not set" not in body["message"]

    async def test_the_nested_shape_is_read_too(self) -> None:
        """Node sends this on `configuration` for some model kinds."""
        config = _llm_config()
        config["configuration"]["contextLength"] = 200_000

        assert _body(await _run_llm_check(config))["capabilities"]["context_length"] == 200_000

    @pytest.mark.parametrize("value", [0, 12, -1, 99_000_000, "eight thousand"])
    async def test_a_value_that_cannot_be_a_window_is_rejected(self, value: object) -> None:
        response = await _run_llm_check(_llm_config(contextLength=value))

        assert response.status_code == 400
        assert "context window" in _body(response)["message"].lower()

    async def test_an_implausible_window_costs_no_provider_call(self) -> None:
        with patch(f"{MODULE}._check_one_llm", new=AsyncMock()) as probe:
            await _run_llm_check(_llm_config(contextLength=5))

        probe.assert_not_awaited()

    @pytest.mark.parametrize("value", ["128000", 1_024, 20_000_000])
    async def test_the_boundaries_and_a_numeric_string_are_accepted(self, value: object) -> None:
        assert (await _run_llm_check(_llm_config(contextLength=value))).status_code == 200


class TestStreamingProbe:
    """Every answer reaches the user through `astream`. A model that only
    supports a blocking call still works, but the whole reply lands at once
    after a long silence — worth reporting, not worth failing."""

    async def test_a_streaming_model_says_so(self) -> None:
        body = _body(await _run_llm_check(_llm_config()))

        assert body["capabilities"]["streaming"] is True
        assert "did not stream" not in body["message"]

    async def test_a_model_that_cannot_stream_is_healthy_with_a_note(self) -> None:
        model = _model(stream_error=NotImplementedError("streaming is not supported"))

        response = await _run_llm_check(_llm_config(), model=model)

        assert response.status_code == 200
        body = _body(response)
        assert body["capabilities"]["streaming"] is False
        assert "did not stream" in body["message"]

    async def test_an_empty_stream_counts_as_not_streaming(self) -> None:
        body = _body(await _run_llm_check(_llm_config(), model=_model(stream_chunks=[])))

        assert body["capabilities"]["streaming"] is False

    async def test_a_transient_failure_does_not_become_a_false_warning(self) -> None:
        """A rate limit says nothing about streaming, and the plain probe has
        already proved the model answers."""
        model = _model(stream_error=_error("429 rate limit exceeded", status=429))

        body = _body(await _run_llm_check(_llm_config(), model=model))

        assert body["capabilities"]["streaming"] is True
