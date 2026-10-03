"""Model health checks refuse endpoints the deployment may not call, before calling them,
and report a provider's failure as fixed text: what the provider said goes to the log only."""

from __future__ import annotations

import builtins
import dis
import ipaddress
import json
import types
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes import health
from app.utils import aimodels
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV
from app.utils.user_messages import action_failed

if TYPE_CHECKING:
    from collections.abc import Iterator

PROVIDER_TEXT = "upstream said: deployment gpt-x not found at https://internal.example/v1"

_PERFORMERS = {
    "llm": "perform_llm_health_check",
    "embedding": "perform_embedding_health_check",
    "imageGeneration": "perform_image_generation_health_check",
    "tts": "perform_tts_health_check",
    "stt": "perform_stt_health_check",
}


def _request() -> MagicMock:
    request = MagicMock()
    request.app.container.logger.return_value = MagicMock()
    return request


def _config(endpoint: str | None) -> dict:
    return {"provider": "openAICompatible", "configuration": {"model": "m", "endpoint": endpoint}}


def _body(response) -> dict:
    return json.loads(response.body)


@pytest.fixture
def default_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
    monkeypatch.delenv("OLLAMA_API_URL", raising=False)


@pytest.fixture
def blocked_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
    monkeypatch.delenv("OLLAMA_API_URL", raising=False)


@pytest.mark.parametrize(("model_type", "performer"), _PERFORMERS.items(), ids=_PERFORMERS.keys())
class TestHealthCheckRoute:
    async def test_metadata_endpoint_is_refused_before_the_check_runs(
        self, default_mode: None, model_type: str, performer: str
    ) -> None:
        with patch.object(health, performer, new_callable=AsyncMock) as perform:
            response = await health.health_check(_request(), model_type, _config("http://169.254.169.254/v1"))

        assert response.status_code == 400
        assert "never allowed" in _body(response)["message"]
        perform.assert_not_awaited()

    async def test_private_endpoint_is_refused_in_blocked_mode(
        self, blocked_mode: None, model_type: str, performer: str
    ) -> None:
        with patch.object(health, performer, new_callable=AsyncMock) as perform:
            response = await health.health_check(_request(), model_type, _config("http://10.0.0.5:8000/v1"))

        assert response.status_code == 400
        assert PRIVATE_ADDRESS_SWITCH_ENV in _body(response)["message"]
        perform.assert_not_awaited()

    async def test_private_endpoint_is_checked_as_usual_by_default(
        self, default_mode: None, model_type: str, performer: str
    ) -> None:
        with patch.object(health, performer, new_callable=AsyncMock, return_value="checked") as perform:
            response = await health.health_check(_request(), model_type, _config("http://localhost:11434"))

        assert response == "checked"
        perform.assert_awaited_once()

    async def test_a_config_without_an_endpoint_is_checked_as_usual(
        self, blocked_mode: None, model_type: str, performer: str
    ) -> None:
        with patch.object(health, performer, new_callable=AsyncMock, return_value="checked") as perform:
            assert await health.health_check(_request(), model_type, _config(None)) == "checked"
        perform.assert_awaited_once()


class TestNamesAreResolved:
    @staticmethod
    def _resolves_to(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
        monkeypatch.setattr(aimodels, "_resolved_addresses", lambda host: [ipaddress.ip_address(address)])

    async def test_a_name_on_a_metadata_address_is_refused_by_default(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._resolves_to(monkeypatch, "169.254.169.254")
        with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock) as perform:
            response = await health.health_check(_request(), "llm", _config("https://models.example/v1"))

        assert response.status_code == 400
        assert "never allowed" in _body(response)["message"]
        perform.assert_not_awaited()

    async def test_a_name_on_a_private_address_is_refused_in_blocked_mode_without_saying_which(
        self, blocked_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._resolves_to(monkeypatch, "10.0.0.5")
        with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock) as perform:
            response = await health.health_check(_request(), "llm", _config("https://models.example/v1"))

        assert response.status_code == 400
        assert PRIVATE_ADDRESS_SWITCH_ENV in _body(response)["message"]
        assert "10.0.0.5" not in _body(response)["message"]
        perform.assert_not_awaited()

    async def test_a_name_on_a_private_address_is_checked_as_usual_by_default(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._resolves_to(monkeypatch, "10.0.0.5")
        with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock, return_value="checked"):
            assert await health.health_check(_request(), "llm", _config("https://models.corp.example/v1")) == "checked"


class TestMalformedConfigs:
    @pytest.mark.parametrize("endpoint", [123, True, ["http://models.example"]])
    async def test_an_endpoint_that_is_not_text_is_a_config_error(self, default_mode: None, endpoint: object) -> None:
        with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock) as perform:
            response = await health.llm_health_check(_request(), [_config(endpoint)])  # type: ignore[arg-type]

        assert response.status_code == 400
        assert "must be a URL" in _body(response)["message"]
        perform.assert_not_awaited()

    @pytest.mark.parametrize("configuration", [None, "model", ["m"]])
    async def test_a_config_without_a_configuration_object_reaches_the_check_that_reports_it(
        self, default_mode: None, configuration: object
    ) -> None:
        ok = MagicMock(status_code=200)
        with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock, return_value=ok) as perform:
            await health.llm_health_check(_request(), [{"provider": "openAI", "configuration": configuration}])

        perform.assert_awaited_once()


class TestBulkRoutes:
    async def test_llm_bulk_check_stops_at_a_refused_endpoint(self, default_mode: None) -> None:
        ok = MagicMock(status_code=200)
        with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock, return_value=ok) as perform:
            response = await health.llm_health_check(
                _request(), [_config("https://api.example/v1"), _config("http://169.254.169.254/v1")]
            )

        assert response.status_code == 400
        assert perform.await_count == 1

    async def test_embedding_bulk_check_refuses_before_building_a_model(self, default_mode: None) -> None:
        with patch.object(health, "initialize_embedding_model", new_callable=AsyncMock) as initialize:
            response = await health.embedding_health_check(_request(), [_config("http://169.254.169.254/v1")])

        assert response.status_code == 400
        initialize.assert_not_awaited()


class TestProviderTextIsNeverReturned:
    async def test_unexpected_failure_returns_fixed_text(self, default_mode: None) -> None:
        request = _request()
        with patch.object(
            health, "perform_llm_health_check", new_callable=AsyncMock, side_effect=RuntimeError(PROVIDER_TEXT)
        ):
            response = await health.health_check(request, "llm", _config("https://api.example/v1"))

        assert response.status_code == 500
        assert _body(response)["error"] == action_failed("check this model")
        assert PROVIDER_TEXT in str(request.app.container.logger.return_value.error.call_args)

    async def test_vision_probe_returns_fixed_text(self) -> None:
        with patch.object(
            health, "_invoke_with_timeout", new_callable=AsyncMock, side_effect=RuntimeError(PROVIDER_TEXT)
        ), patch.object(health, "_is_capability_error", return_value=True), patch.object(
            health, "_get_test_image", return_value="aGk="
        ):
            message = await health._probe_vision(MagicMock(), logger := MagicMock())

        assert message == "Model doesn't support images/vision"
        assert PROVIDER_TEXT in str(logger.info.call_args)

    async def test_image_embedding_probe_returns_fixed_text_when_the_provider_cannot_be_built(self) -> None:
        with patch(
            "app.services.embeddings.multimodal.factory.MultimodalEmbeddingFactory.create",
            side_effect=RuntimeError(PROVIDER_TEXT),
        ):
            message = await health._probe_image_embedding(
                _config("https://api.example/v1"), "m", 8, logger := MagicMock()
            )

        assert message == "Couldn't set up image embedding with these settings"
        assert PROVIDER_TEXT in str(logger.warning.call_args)

    async def test_image_embedding_probe_returns_fixed_text_when_the_model_refuses_images(self) -> None:
        provider = MagicMock()
        provider.supports_multimodal.return_value = True
        provider.embed_images = AsyncMock(side_effect=RuntimeError(PROVIDER_TEXT))
        logger = MagicMock()
        with patch(
            "app.services.embeddings.multimodal.factory.MultimodalEmbeddingFactory.create", return_value=provider
        ), patch.object(health, "_is_capability_error", return_value=True), patch.object(
            health, "_get_test_image", return_value="aGk="
        ):
            message = await health._probe_image_embedding(_config("https://api.example/v1"), "m", 8, logger)

        assert message == "Model cannot embed images"
        assert PROVIDER_TEXT in str(logger.info.call_args)

    async def test_image_embedding_probe_returns_fixed_text_when_the_provider_reports_an_error(self) -> None:
        provider = MagicMock()
        provider.supports_multimodal.return_value = True
        provider.embed_images = AsyncMock(return_value=[MagicMock(embedding=None, error=PROVIDER_TEXT)])
        logger = MagicMock()
        with patch(
            "app.services.embeddings.multimodal.factory.MultimodalEmbeddingFactory.create", return_value=provider
        ), patch.object(health, "_get_test_image", return_value="aGk="):
            message = await health._probe_image_embedding(_config("https://api.example/v1"), "m", 8, logger)

        assert message == "Image embedding returned nothing"
        assert PROVIDER_TEXT in str(logger.warning.call_args)


def _code_objects(code: types.CodeType) -> Iterator[types.CodeType]:
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _code_objects(const)


def test_every_name_the_module_uses_is_defined() -> None:
    """A helper removed while callers remain only fails when that caller runs; catch it here."""
    source = Path(health.__file__).read_text(encoding="utf-8")
    defined = set(vars(health)) | set(vars(builtins))
    undefined: set[str] = set()
    for code in _code_objects(compile(source, health.__file__, "exec")):
        instructions = list(dis.get_instructions(code))
        stored = {i.argval for i in instructions if i.opname in ("STORE_NAME", "STORE_GLOBAL")}
        loaded = {i.argval for i in instructions if i.opname in ("LOAD_NAME", "LOAD_GLOBAL")}
        undefined |= loaded - stored - defined

    assert not undefined
