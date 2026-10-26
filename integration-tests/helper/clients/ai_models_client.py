"""AI Models Configuration API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient

# Adding or updating a provider waits on a live health check of the model. Node gives that
# call 620 s (AI_SERVICE_TIMEOUT_MS in ai.service.command.ts): an LLM is probed for text,
# streaming and tool calls, up to 420 s in all. The client's 60 s default gave up first
# on a slow Azure OpenAI deployment the server would have accepted.
PROVIDER_HEALTH_CHECK_TIMEOUT_S = 630


class AIModelsClient(APIClient):
    """Client for /api/v1/configurationManager/ai-models endpoints.

    Provides methods for managing AI model providers and configurations.
    """

    BASE = "/api/v1/configurationManager/ai-models"

    def get_providers(self) -> requests.Response:
        """Get all AI model providers (admin list)."""
        return self.get("/")

    def get_models_by_type(self, model_type: str) -> requests.Response:
        """Get models by type.

        Args:
            model_type: Model type (llm, embedding, ocr, etc.)
        """
        return self.get(f"/{model_type}")

    def get_available_models(self, model_type: str) -> requests.Response:
        """Get available models for a model type.

        Args:
            model_type: Model type (llm, embedding, etc.)
        """
        return self.get(f"/available/{model_type}")

    def add_provider(self, **kwargs: Any) -> requests.Response:
        """Add a new AI model provider.

        Args:
            **kwargs: Provider fields (providerId, modelType, configuration, etc.)
        """
        return self.post("/providers", json=kwargs, timeout=PROVIDER_HEALTH_CHECK_TIMEOUT_S)

    def update_provider(
        self,
        model_type: str,
        model_key: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Update an AI model provider.

        Args:
            model_type: Model type
            model_key: Model key
            **kwargs: Fields to update
        """
        return self.put(
            f"/providers/{model_type}/{model_key}", json=kwargs, timeout=PROVIDER_HEALTH_CHECK_TIMEOUT_S,
        )

    def delete_provider(
        self,
        model_type: str,
        model_key: str,
    ) -> requests.Response:
        """Delete an AI model provider.

        Args:
            model_type: Model type
            model_key: Model key
        """
        return self.delete(f"/providers/{model_type}/{model_key}")

    def health_check(
        self,
        provider_id: str,
        model_type: str,
        configuration: dict[str, Any],
    ) -> requests.Response:
        """Check health of an AI model provider configuration.

        Args:
            provider_id: Provider ID (openAI, gemini, etc.)
            model_type: Model type
            configuration: Provider configuration
        """
        return self.post(
            "/providers/health-check",
            json={
                "providerId": provider_id,
                "modelType": model_type,
                "configuration": configuration,
            },
        )
