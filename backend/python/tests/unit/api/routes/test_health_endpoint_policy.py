"""Model health checks refuse endpoints the deployment may not call, before calling them,
and report a provider's failure as one short line rather than its whole response."""

from __future__ import annotations

import ipaddress
import json
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes import health
from app.utils import aimodels
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV

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


class TestProviderErrorsStayInTheLog:
    async def test_unexpected_failure_returns_fixed_text(self, default_mode: None) -> None:
        upstream_body = "first line of the provider's answer\n" + "internal detail " * 200
        with patch.object(
            health, "perform_llm_health_check", new_callable=AsyncMock, side_effect=RuntimeError(upstream_body)
        ):
            response = await health.health_check(_request(), "llm", _config("https://api.example/v1"))

        assert response.status_code == 500
        assert _body(response)["error"] == "Health check failed: RuntimeError"

    async def test_failure_without_a_message_still_names_what_failed(self, default_mode: None) -> None:
        with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock, side_effect=RuntimeError()):
            response = await health.health_check(_request(), "llm", _config("https://api.example/v1"))

        assert _body(response)["error"] == "Health check failed: RuntimeError"

    async def test_vision_probe_returns_fixed_text(self) -> None:
        long_error = RuntimeError("image input is not supported\n" + "x" * 5000)
        with patch.object(health, "_invoke_with_timeout", new_callable=AsyncMock, side_effect=long_error), patch.object(
            health, "_is_capability_error", return_value=True
        ), patch.object(health, "_get_test_image", return_value="aGk="):
            message = await health._probe_vision(MagicMock(), MagicMock())

        assert message == "Model doesn't support images/vision."


class TestEndpointOnlyCheck:
    """Model types the bulk save has no health check for are still held to the endpoint policy."""

    async def test_every_config_is_accepted_when_all_endpoints_are_allowed(self, default_mode: None) -> None:
        response = await health.model_endpoint_check([_config("http://localhost:11434"), _config(None)])
        assert response.status_code == 200

    async def test_a_refused_endpoint_anywhere_in_the_list_is_a_config_error(self, default_mode: None) -> None:
        response = await health.model_endpoint_check([_config("https://api.example/v1"), _config("http://169.254.169.254/")])
        assert response.status_code == 400
        assert "never allowed" in _body(response)["message"]

    async def test_a_base_url_is_held_to_the_same_policy(self, default_mode: None) -> None:
        config = {"provider": "ollama", "configuration": {"model": "m", "baseUrl": "http://169.254.169.254/"}}
        response = await health.model_endpoint_check([config])
        assert response.status_code == 400

    async def test_a_name_that_does_not_resolve_is_a_config_error(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def nxdomain(host: str) -> list:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

        monkeypatch.setattr(aimodels, "_resolved_addresses", nxdomain)
        response = await health.model_endpoint_check([_config("https://typo.example/v1")])
        assert response.status_code == 400
        assert "does not resolve" in _body(response)["message"]
