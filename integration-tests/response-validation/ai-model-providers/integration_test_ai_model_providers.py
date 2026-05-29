"""
AI Model Providers API – Integration Tests
=============================================

GET    /api/v1/configurationManager/ai-models  — getAIModelsProviders (admin list;
       not GET .../available/{modelType})
GET    /api/v1/configurationManager/ai-models/{modelType}  — getModelsByType
POST   /api/v1/configurationManager/ai-models/providers  — addAIModelProvider
PUT    /api/v1/configurationManager/ai-models/providers/{modelType}/{modelKey}
       — updateAIModelProvider
DELETE /api/v1/configurationManager/ai-models/providers/{modelType}/{modelKey}

Validates Zod ``addProviderRequestSchema`` / ``updateProviderRequestSchema``,
admin OAuth auth, OpenAPI response schemas in ``pipeshub-openapi.yaml``, and
(when API keys are set) live Python health-check + persist paths for openAI,
gemini, azureOpenAI, and groq.

Provider IDs and required configuration fields mirror the Python registry under
``backend/python/app/config/ai_models/providers/``.

Requires:
  - PIPESHUB_BASE_URL and OAuth credentials (see integration-tests/README.md)
  - Running Node.js API + Python query service (health-check)
  - Per-provider API keys (optional — positive tests skip when unset):
      TEST_OPENAI_API_KEY, TEST_GEMINI_API_KEY, TEST_GROQ_API_KEY,
      TEST_AZURE_OPENAI_API_KEY + TEST_AZURE_OPENAI_ENDPOINT +
      TEST_AZURE_OPENAI_DEPLOYMENT_NAME
  - PIPESHUB_TEST_NON_ADMIN_EMAIL / PIPESHUB_TEST_NON_ADMIN_PASSWORD (optional)
  - PIPESHUB_TEST_DELETE_CONFLICT_MODEL_KEY (optional — 409 when agent uses model)
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
_AUTH_UTILS = _ROOT / "response-validation" / "auth" / "utils"
for _p in (_ROOT, _RV_HELPER, _AUTH_UTILS):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)

logger = logging.getLogger("ai-model-providers-integration-test")

_PROVIDERS_PATH = "/api/v1/configurationManager/ai-models/providers"
_AI_MODELS_PATH = "/api/v1/configurationManager/ai-models"
_MODEL_TYPE_LLM = "llm"
_AI_MODEL_BUCKET_KEYS = (
    "ocr",
    "embedding",
    "slm",
    "llm",
    "reasoning",
    "multiModal",
    "imageGeneration",
    "tts",
    "stt",
)

# Registry provider IDs (camelCase) — must match Python AIModelProviderBuilder ids.
_PROVIDER_OPENAI = "openAI"
_PROVIDER_GEMINI = "gemini"
_PROVIDER_AZURE_OPENAI = "azureOpenAI"
_PROVIDER_GROQ = "groq"


@dataclass(frozen=True)
class LiveProviderSpec:
    """Describes a provider for live (health-check) positive tests."""

    provider_id: str
    api_key_env: str
    api_key_fallback: Optional[str]
    model_env: str
    default_model: str
    build_configuration: Callable[[str, str], Dict[str, Any]]
    required_env: tuple[str, ...] = ()
    skip_reason_extra: str = ""


@dataclass
class CreatedProvider:
    model_type: str
    model_key: str
    provider: str


def _env(name: str, fallback: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, "").strip()
    if value:
        return value
    if fallback:
        fb = os.getenv(fallback, "").strip()
        if fb:
            return fb
    return None


def _admin_headers(client: PipeshubClient) -> Dict[str, str]:
    client._ensure_access_token()
    return {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type": "application/json",
    }


def _providers_url(client: PipeshubClient) -> str:
    return client._url(_PROVIDERS_PATH)


def _ai_models_url(client: PipeshubClient) -> str:
    return client._url(_AI_MODELS_PATH)


def _models_by_type_url(client: PipeshubClient, model_type: str) -> str:
    return client._url(f"{_AI_MODELS_PATH}/{model_type}")


def _delete_provider_url(
    client: PipeshubClient, model_type: str, model_key: str
) -> str:
    return client._url(f"{_PROVIDERS_PATH}/{model_type}/{model_key}")


def _assert_validation_error(body: dict, *, label: str) -> None:
    err = body.get("error") or {}
    assert err.get("code") == "VALIDATION_ERROR", (
        f"[{label}] Expected VALIDATION_ERROR, got {err.get('code')!r}: {body}"
    )
    assert err.get("message") == "Validation failed", (
        f"[{label}] Expected 'Validation failed', got {err.get('message')!r}"
    )


def _assert_error_envelope_matches_spec(body: dict) -> None:
    """Error paths without per-operation response schemas use ErrorResponse."""
    assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")


def _teardown_provider(client: PipeshubClient, created: CreatedProvider) -> None:
    try:
        resp = requests.delete(
            _delete_provider_url(client, created.model_type, created.model_key),
            headers=_admin_headers(client),
            timeout=client.timeout_seconds,
        )
    except Exception as exc:
        logger.warning(
            "Teardown delete failed for modelKey=%s: %s",
            created.model_key,
            exc,
        )
        return
    if resp.status_code >= 300:
        logger.warning(
            "Teardown delete modelKey=%s: HTTP %s %s",
            created.model_key,
            resp.status_code,
            resp.text[:300],
        )


def _live_provider_specs() -> List[LiveProviderSpec]:
    def _openai_config(api_key: str, model: str) -> Dict[str, Any]:
        return {"model": model, "apiKey": api_key}

    def _gemini_config(api_key: str, model: str) -> Dict[str, Any]:
        return {"model": model, "apiKey": api_key}

    def _groq_config(api_key: str, model: str) -> Dict[str, Any]:
        return {"model": model, "apiKey": api_key}

    def _azure_config(api_key: str, model: str) -> Dict[str, Any]:
        endpoint = _env("TEST_AZURE_OPENAI_ENDPOINT") or ""
        deployment = _env("TEST_AZURE_OPENAI_DEPLOYMENT_NAME") or ""
        return {
            "endpoint": endpoint,
            "apiKey": api_key,
            "deploymentName": deployment,
            "model": model,
        }

    return [
        LiveProviderSpec(
            provider_id=_PROVIDER_OPENAI,
            api_key_env="TEST_OPENAI_API_KEY",
            api_key_fallback="OPENAI_API_KEY",
            model_env="TEST_OPENAI_LLM_MODEL",
            default_model="gpt-4o-mini",
            build_configuration=_openai_config,
        ),
        LiveProviderSpec(
            provider_id=_PROVIDER_GEMINI,
            api_key_env="TEST_GEMINI_API_KEY",
            api_key_fallback=None,
            model_env="TEST_GEMINI_LLM_MODEL",
            default_model="gemini-2.0-flash",
            build_configuration=_gemini_config,
        ),
        LiveProviderSpec(
            provider_id=_PROVIDER_AZURE_OPENAI,
            api_key_env="TEST_AZURE_OPENAI_API_KEY",
            api_key_fallback=None,
            model_env="TEST_AZURE_OPENAI_MODEL",
            default_model="gpt-4o",
            build_configuration=_azure_config,
            required_env=(
                "TEST_AZURE_OPENAI_ENDPOINT",
                "TEST_AZURE_OPENAI_DEPLOYMENT_NAME",
            ),
            skip_reason_extra=" (endpoint + deployment required)",
        ),
        LiveProviderSpec(
            provider_id=_PROVIDER_GROQ,
            api_key_env="TEST_GROQ_API_KEY",
            api_key_fallback=None,
            model_env="TEST_GROQ_LLM_MODEL",
            default_model="llama-3.3-70b-versatile",
            build_configuration=_groq_config,
        ),
    ]


def _resolve_live_spec(spec: LiveProviderSpec) -> Optional[tuple[str, str, Dict[str, Any]]]:
    """Return (api_key, model_name, configuration) or None if env is incomplete."""
    api_key = _env(spec.api_key_env, spec.api_key_fallback)
    if not api_key:
        return None
    for name in spec.required_env:
        if not _env(name):
            return None
    model = _env(spec.model_env) or spec.default_model
    configuration = spec.build_configuration(api_key, model)
    if spec.provider_id == _PROVIDER_AZURE_OPENAI:
        if not configuration.get("endpoint") or not configuration.get("deploymentName"):
            return None
    return api_key, model, configuration


def _skip_if_no_live_credentials(spec: LiveProviderSpec) -> tuple[str, str, Dict[str, Any]]:
    resolved = _resolve_live_spec(spec)
    if resolved is None:
        missing = [spec.api_key_env, *spec.required_env]
        pytest.skip(
            f"{spec.provider_id}: set {', '.join(missing)}{spec.skip_reason_extra}"
        )
    _, model, configuration = resolved
    return spec.provider_id, model, configuration


def _minimal_llm_payload(
    provider: str,
    configuration: Dict[str, Any],
    *,
    is_default: bool = False,
    is_multimodal: bool = False,
    is_reasoning: bool = False,
    context_length: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "modelType": _MODEL_TYPE_LLM,
        "provider": provider,
        "configuration": configuration,
        "isMultimodal": is_multimodal,
        "isReasoning": is_reasoning,
        "isDefault": is_default,
    }
    if context_length is not None:
        payload["contextLength"] = context_length
    return payload


def _post_provider(
    client: PipeshubClient,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    raw_data: Optional[str] = None,
) -> requests.Response:
    kwargs: Dict[str, Any] = {
        "url": _providers_url(client),
        "timeout": client.timeout_seconds,
    }
    if raw_data is not None:
        kwargs["data"] = raw_data
        kwargs["headers"] = headers or {"Content-Type": "application/json"}
    else:
        kwargs["json"] = payload
        kwargs["headers"] = headers or _admin_headers(client)
    return requests.post(**kwargs)


def _put_provider(
    client: PipeshubClient,
    model_type: str,
    model_key: str,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    raw_data: Optional[str] = None,
) -> requests.Response:
    url = client._url(f"{_PROVIDERS_PATH}/{model_type}/{model_key}")
    kwargs: Dict[str, Any] = {"url": url, "timeout": client.timeout_seconds}
    if raw_data is not None:
        kwargs["data"] = raw_data
        kwargs["headers"] = headers or {"Content-Type": "application/json"}
    else:
        kwargs["json"] = payload
        kwargs["headers"] = headers or _admin_headers(client)
    return requests.put(**kwargs)


def _delete_provider(
    client: PipeshubClient,
    model_type: str,
    model_key: str,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    return requests.delete(
        _delete_provider_url(client, model_type, model_key),
        headers=headers or _admin_headers(client),
        timeout=client.timeout_seconds,
    )


def _get_ai_models(
    client: PipeshubClient,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    return requests.get(
        _ai_models_url(client),
        headers=headers or _admin_headers(client),
        timeout=client.timeout_seconds,
    )


def _get_models_by_type(
    client: PipeshubClient,
    model_type: str,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    return requests.get(
        _models_by_type_url(client, model_type),
        headers=headers or _admin_headers(client),
        timeout=client.timeout_seconds,
    )


def _assert_all_models_bucket_structure(body: dict, *, label: str) -> None:
    assert body.get("status") == "success", f"[{label}] Unexpected status: {body}"
    models = body.get("models")
    assert isinstance(models, dict), f"[{label}] models must be an object: {body}"
    for key in _AI_MODEL_BUCKET_KEYS:
        assert key in models, f"[{label}] Missing models.{key}"
        assert isinstance(models[key], list), (
            f"[{label}] models.{key} must be a list, got {type(models[key])!r}"
        )
    assert isinstance(body.get("message"), str) and body["message"], (
        f"[{label}] Expected non-empty message: {body}"
    )


def _find_configured_model(
    models: List[Dict[str, Any]], model_key: str
) -> Optional[Dict[str, Any]]:
    return next((m for m in models if m.get("modelKey") == model_key), None)


def _minimal_update_body(
    provider: str,
    configuration: Dict[str, Any],
    *,
    is_default: bool = False,
    is_multimodal: bool = False,
    is_reasoning: bool = False,
    context_length: Optional[int] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "provider": provider,
        "configuration": configuration,
        "isMultimodal": is_multimodal,
        "isReasoning": is_reasoning,
        "isDefault": is_default,
    }
    if context_length is not None:
        body["contextLength"] = context_length
    return body


def _create_live_provider(
    client: PipeshubClient,
    spec: LiveProviderSpec,
    *,
    headers: Dict[str, str],
) -> CreatedProvider:
    """POST a provider using live credentials; caller must teardown."""
    provider_id, _model, configuration = _skip_if_no_live_credentials(spec)
    payload = _minimal_llm_payload(provider_id, configuration)
    resp = _post_provider(client, payload, headers=headers)
    assert resp.status_code == 200, (
        f"[{provider_id}] create failed: {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    details = body.get("details") or {}
    model_key = details.get("modelKey")
    assert model_key, f"[{provider_id}] Missing details.modelKey: {body}"
    return CreatedProvider(
        model_type=_MODEL_TYPE_LLM,
        model_key=model_key,
        provider=provider_id,
    )


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture(scope="module")
def providers_api(pipeshub_client: PipeshubClient) -> Dict[str, Any]:
    """Shared URL, timeout, and admin headers for this module."""
    return {
        "client": pipeshub_client,
        "url": _providers_url(pipeshub_client),
        "timeout": pipeshub_client.timeout_seconds,
        "headers": _admin_headers(pipeshub_client),
    }


# ==================================================================== #
# Negative — validation & auth (no live API keys)
# ==================================================================== #


@pytest.mark.integration
class TestAddAIModelProviderValidation:
    """Invalid bodies rejected by ValidationMiddleware (Zod) before health-check."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client: PipeshubClient = providers_api["client"]
        self.headers = providers_api["headers"]
        self.timeout = providers_api["timeout"]

    @pytest.mark.parametrize(
        "label,payload",
        [
            (
                "missing modelType",
                {
                    "provider": _PROVIDER_OPENAI,
                    "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                },
            ),
            (
                "missing provider",
                {
                    "modelType": _MODEL_TYPE_LLM,
                    "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                },
            ),
            (
                "missing configuration",
                {
                    "modelType": _MODEL_TYPE_LLM,
                    "provider": _PROVIDER_OPENAI,
                },
            ),
            (
                "invalid modelType enum",
                {
                    "modelType": "not-a-real-type",
                    "provider": _PROVIDER_OPENAI,
                    "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                },
            ),
            (
                "empty provider string",
                {
                    "modelType": _MODEL_TYPE_LLM,
                    "provider": "",
                    "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                },
            ),
            (
                "modelFriendlyName with comma-separated models",
                {
                    "modelType": _MODEL_TYPE_LLM,
                    "provider": _PROVIDER_OPENAI,
                    "configuration": {
                        "model": "gpt-4o-mini, gpt-4o",
                        "apiKey": "sk-test",
                        "modelFriendlyName": "Should not be allowed",
                    },
                },
            ),
            (
                "contextLength wrong type",
                {
                    "modelType": _MODEL_TYPE_LLM,
                    "provider": _PROVIDER_OPENAI,
                    "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                    "contextLength": "not-a-number",
                },
            ),
        ],
    )
    def test_validation_rejects_invalid_body(
        self, label: str, payload: Dict[str, Any]
    ) -> None:
        resp = _post_provider(self.client, payload, headers=self.headers)
        assert resp.status_code == 400, (
            f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        _assert_validation_error(body, label=label)
        _assert_error_envelope_matches_spec(body)

    def test_malformed_json_body(self) -> None:
        resp = _post_provider(
            self.client,
            {},
            headers=self.headers,
            raw_data='{"modelType": "llm", "provider": "openAI", broken}',
        )
        assert resp.status_code >= 400, (
            f"Expected client error for malformed JSON, got {resp.status_code}: {resp.text}"
        )

    def test_missing_authorization(self) -> None:
        payload = _minimal_llm_payload(
            _PROVIDER_OPENAI,
            {"model": "gpt-4o-mini", "apiKey": "sk-test"},
        )
        resp = requests.post(
            _providers_url(self.client),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (400, 401), (
            f"Expected 400/401 without Authorization, got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 401:
            _assert_error_envelope_matches_spec(resp.json())


@pytest.mark.integration
class TestAddAIModelProviderHealthCheckFailure:
    """Health-check failures for invalid credentials (no valid API key required)."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]

    @pytest.mark.slow
    @pytest.mark.parametrize(
        "provider,configuration",
        [
            (
                _PROVIDER_OPENAI,
                {"model": "gpt-4o-mini", "apiKey": "sk-invalid-integration-test"},
            ),
            (
                _PROVIDER_GEMINI,
                {"model": "gemini-2.0-flash", "apiKey": "invalid-gemini-key"},
            ),
            (
                _PROVIDER_GROQ,
                {
                    "model": "llama-3.3-70b-versatile",
                    "apiKey": "gsk_invalid_integration_test",
                },
            ),
        ],
    )
    def test_invalid_api_key_fails_health_check(
        self, provider: str, configuration: Dict[str, Any]
    ) -> None:
        payload = _minimal_llm_payload(provider, configuration)
        resp = _post_provider(self.client, payload, headers=self.headers)
        assert resp.status_code in (400, 401, 422, 500), (
            f"[{provider}] Expected health-check failure status, "
            f"got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        if resp.status_code == 200:
            pytest.fail(f"[{provider}] Unexpected success with invalid API key: {body}")
        # Controller wraps health-check errors under `error`
        err = body.get("error") or body
        assert err.get("status") == "error" or err.get("message") or body.get("message"), (
            f"[{provider}] Expected error payload, got {body}"
        )


# ==================================================================== #
# Positive — live providers (skip when env keys missing)
# ==================================================================== #


@pytest.mark.integration
@pytest.mark.slow
class TestAddAIModelProviderLive:
    """Successful add + modelKey assignment; tears down via DELETE."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]

    @pytest.mark.parametrize("spec", _live_provider_specs(), ids=lambda s: s.provider_id)
    def test_add_minimal_llm_provider(
        self, spec: LiveProviderSpec
    ) -> None:
        provider_id, _model, configuration = _skip_if_no_live_credentials(spec)
        payload = _minimal_llm_payload(provider_id, configuration)
        created: Optional[CreatedProvider] = None
        try:
            resp = _post_provider(self.client, payload, headers=self.headers)
            assert resp.status_code == 200, (
                f"[{provider_id}] Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("status") == "success", body
            details = body.get("details") or {}
            model_key = details.get("modelKey")
            assert model_key, f"[{provider_id}] Missing details.modelKey: {body}"
            assert details.get("modelType") == _MODEL_TYPE_LLM
            assert details.get("provider") == provider_id
            assert_response_matches_openapi_operation(
                body, "addAIModelProvider", status_code="200"
            )
            created = CreatedProvider(
                model_type=_MODEL_TYPE_LLM,
                model_key=model_key,
                provider=provider_id,
            )
        finally:
            if created:
                _teardown_provider(self.client, created)

    @pytest.mark.parametrize("spec", _live_provider_specs(), ids=lambda s: s.provider_id)
    def test_add_llm_with_optional_flags(
        self, spec: LiveProviderSpec
    ) -> None:
        provider_id, _model, configuration = _skip_if_no_live_credentials(spec)
        configuration = {
            **configuration,
            "modelFriendlyName": f"IT {provider_id} {uuid.uuid4().hex[:8]}",
        }
        payload = _minimal_llm_payload(
            provider_id,
            configuration,
            is_default=False,
            is_multimodal=provider_id in (_PROVIDER_GEMINI, _PROVIDER_OPENAI),
            is_reasoning=False,
            context_length=128000,
        )
        created: Optional[CreatedProvider] = None
        try:
            resp = _post_provider(self.client, payload, headers=self.headers)
            assert resp.status_code == 200, (
                f"[{provider_id} optional flags] "
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(
                body, "addAIModelProvider", status_code="200"
            )
            details = body.get("details") or {}
            model_key = details.get("modelKey")
            assert model_key
            created = CreatedProvider(
                model_type=_MODEL_TYPE_LLM,
                model_key=model_key,
                provider=provider_id,
            )
        finally:
            if created:
                _teardown_provider(self.client, created)


@pytest.mark.integration
class TestAddAIModelProviderAzureFields:
    """Azure OpenAI requires endpoint, apiKey, deploymentName per registry."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]

    def test_azure_missing_deployment_rejected_at_health_check(self) -> None:
        api_key = _env("TEST_AZURE_OPENAI_API_KEY")
        endpoint = _env("TEST_AZURE_OPENAI_ENDPOINT")
        if not api_key or not endpoint:
            pytest.skip(
                "TEST_AZURE_OPENAI_API_KEY and TEST_AZURE_OPENAI_ENDPOINT required"
            )
        payload = _minimal_llm_payload(
            _PROVIDER_AZURE_OPENAI,
            {
                "endpoint": endpoint,
                "apiKey": api_key,
                "model": _env("TEST_AZURE_OPENAI_MODEL") or "gpt-4o",
                # deploymentName intentionally omitted
            },
        )
        resp = _post_provider(self.client, payload, headers=self.headers)
        assert resp.status_code in (400, 422, 500), (
            f"Expected failure without deploymentName, got {resp.status_code}: {resp.text}"
        )


@pytest.mark.integration
class TestAddAIModelProviderNonAdmin:
    """Admin gate via userAdminCheck — skipped when no non-admin fixture exists."""

    def test_non_admin_rejected(self, pipeshub_client: PipeshubClient) -> None:
        if not os.getenv("PIPESHUB_TEST_NON_ADMIN_EMAIL") or not os.getenv(
            "PIPESHUB_TEST_NON_ADMIN_PASSWORD"
        ):
            pytest.skip(
                "No non-admin fixture: set PIPESHUB_TEST_NON_ADMIN_EMAIL and "
                "PIPESHUB_TEST_NON_ADMIN_PASSWORD to enable this test"
            )
        from auth_helpers import login_with_user, session_headers  # noqa: E402

        token, _ = login_with_user(
            pipeshub_client.base_url,
            os.environ["PIPESHUB_TEST_NON_ADMIN_EMAIL"].strip(),
            os.environ["PIPESHUB_TEST_NON_ADMIN_PASSWORD"].strip(),
            pipeshub_client.timeout_seconds,
        )
        payload = _minimal_llm_payload(
            _PROVIDER_OPENAI,
            {"model": "gpt-4o-mini", "apiKey": "sk-test"},
        )
        resp = requests.post(
            _providers_url(pipeshub_client),
            json=payload,
            headers=session_headers(token),
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"Expected 400 Admin access required, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("error", {}).get("message") == "Admin access required", body
        _assert_error_envelope_matches_spec(body)


# ==================================================================== #
# PUT — validation & auth (no live API keys)
# ==================================================================== #


@pytest.mark.integration
class TestUpdateAIModelProviderValidation:
    """Invalid params/bodies rejected by Zod before health-check."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]
        self.timeout = providers_api["timeout"]

    @pytest.mark.parametrize(
        "label,model_type,model_key,payload",
        [
            (
                "invalid modelType path",
                "not-a-real-type",
                str(uuid.uuid4()),
                _minimal_update_body(
                    _PROVIDER_OPENAI,
                    {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                ),
            ),
            (
                "missing provider",
                _MODEL_TYPE_LLM,
                str(uuid.uuid4()),
                {
                    "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                    "isMultimodal": False,
                    "isReasoning": False,
                    "isDefault": False,
                },
            ),
            (
                "missing configuration",
                _MODEL_TYPE_LLM,
                str(uuid.uuid4()),
                {
                    "provider": _PROVIDER_OPENAI,
                    "isMultimodal": False,
                    "isReasoning": False,
                    "isDefault": False,
                },
            ),
            (
                "empty provider",
                _MODEL_TYPE_LLM,
                str(uuid.uuid4()),
                _minimal_update_body(
                    "",
                    {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                ),
            ),
            (
                "modelFriendlyName with comma-separated models",
                _MODEL_TYPE_LLM,
                str(uuid.uuid4()),
                _minimal_update_body(
                    _PROVIDER_OPENAI,
                    {
                        "model": "gpt-4o-mini, gpt-4o",
                        "apiKey": "sk-test",
                        "modelFriendlyName": "Not allowed",
                    },
                ),
            ),
            (
                "contextLength wrong type",
                _MODEL_TYPE_LLM,
                str(uuid.uuid4()),
                {
                    "provider": _PROVIDER_OPENAI,
                    "configuration": {"model": "gpt-4o-mini", "apiKey": "sk-test"},
                    "isMultimodal": False,
                    "isReasoning": False,
                    "isDefault": False,
                    "contextLength": "not-a-number",
                },
            ),
        ],
    )
    def test_validation_rejects_invalid_put(
        self,
        label: str,
        model_type: str,
        model_key: str,
        payload: Dict[str, Any],
    ) -> None:
        resp = _put_provider(
            self.client, model_type, model_key, payload, headers=self.headers
        )
        assert resp.status_code == 400, (
            f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        _assert_validation_error(body, label=label)
        _assert_error_envelope_matches_spec(body)

    def test_empty_model_key_put_returns_404(self) -> None:
        # PUT .../providers/:modelType/:modelKey with modelKey="" becomes
        # .../llm/ (trailing slash). Express does not match that path, so Zod
        # (updateProviderRequestSchema modelKey min 1) never runs — 404, not 400.
        payload = _minimal_update_body(
            _PROVIDER_OPENAI,
            {"model": "gpt-4o-mini", "apiKey": "sk-test"},
        )
        resp = _put_provider(
            self.client, _MODEL_TYPE_LLM, "", payload, headers=self.headers
        )
        assert resp.status_code == 404, (
            f"Expected 404 for empty modelKey (no route match), "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_unknown_model_key_returns_404(self) -> None:
        # updateAIModelProvider runs Python health-check before KV lookup; use
        # real credentials so a missing modelKey yields 404, not health-check 4xx/5xx.
        openai_spec = next(
            s for s in _live_provider_specs() if s.provider_id == _PROVIDER_OPENAI
        )
        _, _, configuration = _skip_if_no_live_credentials(openai_spec)
        unknown_key = str(uuid.uuid4())
        payload = _minimal_update_body(_PROVIDER_OPENAI, configuration)
        resp = _put_provider(
            self.client, _MODEL_TYPE_LLM, unknown_key, payload, headers=self.headers
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown modelKey, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("status") == "error" or body.get("error"), body
        assert "not found" in (body.get("message") or "").lower(), body

    def test_missing_authorization(self) -> None:
        payload = _minimal_update_body(
            _PROVIDER_OPENAI,
            {"model": "gpt-4o-mini", "apiKey": "sk-test"},
        )
        resp = requests.put(
            self.client._url(
                f"{_PROVIDERS_PATH}/{_MODEL_TYPE_LLM}/{uuid.uuid4()}"
            ),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (400, 401), (
            f"Expected 400/401 without Authorization, got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 401:
            _assert_error_envelope_matches_spec(resp.json())

    def test_malformed_json_body(self) -> None:
        resp = _put_provider(
            self.client,
            _MODEL_TYPE_LLM,
            str(uuid.uuid4()),
            {},
            headers=self.headers,
            raw_data='{"provider": "openAI", broken}',
        )
        assert resp.status_code >= 400, (
            f"Expected client error for malformed JSON, got {resp.status_code}: {resp.text}"
        )


@pytest.mark.integration
class TestUpdateAIModelProviderNonAdmin:
    """PUT rejected for non-admin session JWT."""

    def test_non_admin_put_rejected(self, pipeshub_client: PipeshubClient) -> None:
        if not os.getenv("PIPESHUB_TEST_NON_ADMIN_EMAIL") or not os.getenv(
            "PIPESHUB_TEST_NON_ADMIN_PASSWORD"
        ):
            pytest.skip(
                "Set PIPESHUB_TEST_NON_ADMIN_EMAIL and "
                "PIPESHUB_TEST_NON_ADMIN_PASSWORD to enable this test"
            )
        from auth_helpers import login_with_user, session_headers  # noqa: E402

        token, _ = login_with_user(
            pipeshub_client.base_url,
            os.environ["PIPESHUB_TEST_NON_ADMIN_EMAIL"].strip(),
            os.environ["PIPESHUB_TEST_NON_ADMIN_PASSWORD"].strip(),
            pipeshub_client.timeout_seconds,
        )
        payload = _minimal_update_body(
            _PROVIDER_OPENAI,
            {"model": "gpt-4o-mini", "apiKey": "sk-test"},
        )
        resp = requests.put(
            pipeshub_client._url(
                f"{_PROVIDERS_PATH}/{_MODEL_TYPE_LLM}/{uuid.uuid4()}"
            ),
            json=payload,
            headers=session_headers(token),
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"Expected 400 Admin access required, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("error", {}).get("message") == "Admin access required", body
        _assert_error_envelope_matches_spec(body)


# ==================================================================== #
# PUT — live create → update → delete
# ==================================================================== #


@pytest.mark.integration
@pytest.mark.slow
class TestUpdateAIModelProviderLive:
    """Successful update after POST; OpenAPI + teardown via DELETE."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]

    @pytest.mark.parametrize("spec", _live_provider_specs(), ids=lambda s: s.provider_id)
    def test_create_update_delete_llm_provider(self, spec: LiveProviderSpec) -> None:
        created: Optional[CreatedProvider] = None
        try:
            created = _create_live_provider(
                self.client, spec, headers=self.headers
            )
            provider_id, _model, configuration = _skip_if_no_live_credentials(spec)
            updated_config = {
                **configuration,
                "modelFriendlyName": f"Updated {provider_id} {uuid.uuid4().hex[:8]}",
            }
            update_body = _minimal_update_body(
                provider_id,
                updated_config,
                is_multimodal=provider_id == _PROVIDER_GEMINI,
                context_length=64000,
            )
            resp = _put_provider(
                self.client,
                created.model_type,
                created.model_key,
                update_body,
                headers=self.headers,
            )
            assert resp.status_code == 200, (
                f"[{provider_id}] PUT expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("status") == "success", body
            details = body.get("details") or {}
            assert details.get("modelKey") == created.model_key
            assert details.get("modelType") == _MODEL_TYPE_LLM
            assert_response_matches_openapi_operation(
                body, "updateAIModelProvider", status_code="200"
            )
        finally:
            if created:
                _teardown_provider(self.client, created)

    def test_model_type_mismatch_returns_400(
        self, providers_api: Dict[str, Any]
    ) -> None:
        """PUT embedding path + LLM modelKey: health-check runs before KV mismatch check.

        updateAIModelProvider validates provider/config, then calls the AI health-check
        with the path modelType (embedding). An LLM model fails that check; the
        controller never reaches the 'belongs to type' 400 branch. Matches documented
        controller order (not a product bug fix target for this test suite).
        """
        openai_spec = next(
            s for s in _live_provider_specs() if s.provider_id == _PROVIDER_OPENAI
        )
        client = providers_api["client"]
        headers = providers_api["headers"]
        created: Optional[CreatedProvider] = None
        try:
            created = _create_live_provider(client, openai_spec, headers=headers)
            _, _, configuration = _skip_if_no_live_credentials(openai_spec)
            update_body = _minimal_update_body(_PROVIDER_OPENAI, configuration)
            resp = _put_provider(
                client,
                "embedding",
                created.model_key,
                update_body,
                headers=headers,
            )
            assert resp.status_code in (400, 401, 422, 500), (
                f"Expected health-check failure before type mismatch, "
                f"got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            message = (
                body.get("error", {}).get("message")
                or body.get("message")
                or ""
            )
            assert "belongs to type" not in message, body
        finally:
            if created:
                _teardown_provider(client, created)

    @pytest.mark.parametrize("spec", _live_provider_specs(), ids=lambda s: s.provider_id)
    def test_update_invalid_api_key_fails_health_check(
        self, spec: LiveProviderSpec
    ) -> None:
        created: Optional[CreatedProvider] = None
        try:
            created = _create_live_provider(
                self.client, spec, headers=self.headers
            )
            bad_config = {"model": "gpt-4o-mini", "apiKey": "sk-invalid-update-test"}
            if spec.provider_id == _PROVIDER_GEMINI:
                bad_config = {
                    "model": "gemini-2.0-flash",
                    "apiKey": "invalid-gemini-key",
                }
            elif spec.provider_id == _PROVIDER_GROQ:
                bad_config = {
                    "model": "llama-3.3-70b-versatile",
                    "apiKey": "gsk_invalid_update_test",
                }
            update_body = _minimal_update_body(spec.provider_id, bad_config)
            resp = _put_provider(
                self.client,
                created.model_type,
                created.model_key,
                update_body,
                headers=self.headers,
            )
            assert resp.status_code in (400, 401, 422, 500), (
                f"[{spec.provider_id}] Expected health-check failure on PUT, "
                f"got {resp.status_code}: {resp.text}"
            )
        finally:
            if created:
                _teardown_provider(self.client, created)


# ==================================================================== #
# DELETE — validation & auth (no live API keys)
# ==================================================================== #


@pytest.mark.integration
class TestDeleteAIModelProviderValidation:
    """Invalid path params rejected by Zod (deleteProviderSchema)."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]
        self.timeout = providers_api["timeout"]

    @pytest.mark.parametrize(
        "label,model_type,model_key",
        [
            ("invalid modelType path", "not-a-real-type", str(uuid.uuid4())),
        ],
    )
    def test_validation_rejects_invalid_delete(
        self, label: str, model_type: str, model_key: str
    ) -> None:
        resp = _delete_provider(
            self.client, model_type, model_key, headers=self.headers
        )
        assert resp.status_code == 400, (
            f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        _assert_validation_error(body, label=label)
        _assert_error_envelope_matches_spec(body)

    def test_empty_modelKey_delete_returns_404(self) -> None:
        # Express does not match DELETE .../providers/:modelType/:modelKey when
        # modelKey is empty (URL ends with .../llm/); deleteProviderSchema min(1)
        # is never reached — same routing limitation as PUT empty modelKey.
        resp = _delete_provider(
            self.client, _MODEL_TYPE_LLM, "", headers=self.headers
        )
        assert resp.status_code == 404, (
            f"Expected 404 (no route match for empty modelKey), "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_unknown_model_key_returns_404(self) -> None:
        unknown_key = str(uuid.uuid4())
        resp = _delete_provider(
            self.client, _MODEL_TYPE_LLM, unknown_key, headers=self.headers
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown modelKey, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("status") == "error" or body.get("error"), body

    def test_missing_authorization(self) -> None:
        resp = requests.delete(
            _delete_provider_url(self.client, _MODEL_TYPE_LLM, str(uuid.uuid4())),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (400, 401), (
            f"Expected 400/401 without Authorization, got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 401:
            _assert_error_envelope_matches_spec(resp.json())


@pytest.mark.integration
class TestDeleteAIModelProviderNonAdmin:
    """DELETE rejected for non-admin session JWT."""

    def test_non_admin_delete_rejected(self, pipeshub_client: PipeshubClient) -> None:
        if not os.getenv("PIPESHUB_TEST_NON_ADMIN_EMAIL") or not os.getenv(
            "PIPESHUB_TEST_NON_ADMIN_PASSWORD"
        ):
            pytest.skip(
                "Set PIPESHUB_TEST_NON_ADMIN_EMAIL and "
                "PIPESHUB_TEST_NON_ADMIN_PASSWORD to enable this test"
            )
        from auth_helpers import login_with_user, session_headers  # noqa: E402

        token, _ = login_with_user(
            pipeshub_client.base_url,
            os.environ["PIPESHUB_TEST_NON_ADMIN_EMAIL"].strip(),
            os.environ["PIPESHUB_TEST_NON_ADMIN_PASSWORD"].strip(),
            pipeshub_client.timeout_seconds,
        )
        resp = requests.delete(
            _delete_provider_url(pipeshub_client, _MODEL_TYPE_LLM, str(uuid.uuid4())),
            headers=session_headers(token),
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"Expected 400 Admin access required, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("error", {}).get("message") == "Admin access required", body
        _assert_error_envelope_matches_spec(body)


# ==================================================================== #
# DELETE — live create → delete
# ==================================================================== #


@pytest.mark.integration
@pytest.mark.slow
class TestDeleteAIModelProviderLive:
    """Successful DELETE after POST; OpenAPI status + teardown safety."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]

    @pytest.mark.parametrize("spec", _live_provider_specs(), ids=lambda s: s.provider_id)
    def test_create_then_delete_llm_provider(self, spec: LiveProviderSpec) -> None:
        created: Optional[CreatedProvider] = None
        try:
            created = _create_live_provider(
                self.client, spec, headers=self.headers
            )
            resp = _delete_provider(
                self.client,
                created.model_type,
                created.model_key,
                headers=self.headers,
            )
            assert resp.status_code == 200, (
                f"[{spec.provider_id}] DELETE expected 200, "
                f"got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("status") == "success", body
            details = body.get("details") or {}
            assert details.get("modelKey") == created.model_key
            assert details.get("modelType") == _MODEL_TYPE_LLM
            assert details.get("provider") == created.provider
            deleted_key = created.model_key
            created = None
            second = _delete_provider(
                self.client,
                _MODEL_TYPE_LLM,
                deleted_key,
                headers=self.headers,
            )
            assert second.status_code == 404, (
                f"Expected 404 on second DELETE, got {second.status_code}: {second.text}"
            )
        finally:
            if created:
                _teardown_provider(self.client, created)

    def test_model_type_mismatch_returns_400(
        self, providers_api: Dict[str, Any]
    ) -> None:
        """Model exists under llm but path uses embedding → 400."""
        openai_spec = next(
            s for s in _live_provider_specs() if s.provider_id == _PROVIDER_OPENAI
        )
        client = providers_api["client"]
        headers = providers_api["headers"]
        created: Optional[CreatedProvider] = None
        try:
            created = _create_live_provider(client, openai_spec, headers=headers)
            resp = _delete_provider(
                client,
                "embedding",
                created.model_key,
                headers=headers,
            )
            assert resp.status_code == 400, (
                f"Expected 400 model type mismatch, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "belongs to type" in (body.get("message") or ""), body
        finally:
            if created:
                _teardown_provider(client, created)


@pytest.mark.integration
class TestDeleteAIModelProviderConflict:
    """409 when an agent references the model (optional — requires env fixture)."""

    def test_delete_blocked_when_agent_uses_model(
        self, providers_api: Dict[str, Any]
    ) -> None:
        model_key = os.getenv("PIPESHUB_TEST_DELETE_CONFLICT_MODEL_KEY", "").strip()
        if not model_key:
            pytest.skip(
                "Set PIPESHUB_TEST_DELETE_CONFLICT_MODEL_KEY to an LLM modelKey "
                "that is referenced by at least one agent to test 409 conflict"
            )
        client = providers_api["client"]
        headers = providers_api["headers"]
        resp = _delete_provider(
            client, _MODEL_TYPE_LLM, model_key, headers=headers
        )
        assert resp.status_code == 409, (
            f"Expected 409 when model is in use, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        message = body.get("error", {}).get("message") or body.get("message") or ""
        assert "currently in use" in message, body
        assert body.get("error", {}).get("code") == "HTTP_CONFLICT", body
        _assert_error_envelope_matches_spec(body)


# ==================================================================== #
# GET — list all providers (admin /ai-models, not /available/llm)
# ==================================================================== #


@pytest.mark.integration
class TestGetAIModelsProviders:
    """GET /configurationManager/ai-models — getAIModelsProviders."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]
        self.timeout = providers_api["timeout"]

    def test_list_all_returns_200_with_bucket_structure(self) -> None:
        resp = _get_ai_models(self.client, headers=self.headers)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        _assert_all_models_bucket_structure(body, label="list-all")
        if body.get("message") == "No AI models found":
            for key in _AI_MODEL_BUCKET_KEYS:
                assert body["models"][key] == [], (
                    f"Empty org should have empty models.{key}"
                )

    def test_list_all_openapi_contract(self) -> None:
        """May fail if spec stays loose `type: object` — intentional drift signal."""
        resp = _get_ai_models(self.client, headers=self.headers)
        assert resp.status_code == 200, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getAIModelsProviders", status_code="200"
        )

    def test_missing_authorization(self) -> None:
        resp = requests.get(
            _ai_models_url(self.client),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (400, 401), (
            f"Expected 400/401 without Authorization, got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 401:
            _assert_error_envelope_matches_spec(resp.json())


@pytest.mark.integration
class TestGetAIModelsProvidersNonAdmin:
    """GET /ai-models rejected for non-admin session JWT."""

    def test_non_admin_list_rejected(self, pipeshub_client: PipeshubClient) -> None:
        if not os.getenv("PIPESHUB_TEST_NON_ADMIN_EMAIL") or not os.getenv(
            "PIPESHUB_TEST_NON_ADMIN_PASSWORD"
        ):
            pytest.skip(
                "Set PIPESHUB_TEST_NON_ADMIN_EMAIL and "
                "PIPESHUB_TEST_NON_ADMIN_PASSWORD to enable this test"
            )
        from auth_helpers import login_with_user, session_headers  # noqa: E402

        token, _ = login_with_user(
            pipeshub_client.base_url,
            os.environ["PIPESHUB_TEST_NON_ADMIN_EMAIL"].strip(),
            os.environ["PIPESHUB_TEST_NON_ADMIN_PASSWORD"].strip(),
            pipeshub_client.timeout_seconds,
        )
        resp = requests.get(
            _ai_models_url(pipeshub_client),
            headers=session_headers(token),
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"Expected 400 Admin access required, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("error", {}).get("message") == "Admin access required", body
        _assert_error_envelope_matches_spec(body)


@pytest.mark.integration
@pytest.mark.slow
class TestGetAIModelsProvidersLive:
    """POST provider then GET list-all includes entry under models.llm."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]

    @pytest.mark.parametrize("spec", _live_provider_specs(), ids=lambda s: s.provider_id)
    def test_list_all_includes_created_llm_provider(
        self, spec: LiveProviderSpec
    ) -> None:
        created: Optional[CreatedProvider] = None
        try:
            created = _create_live_provider(self.client, spec, headers=self.headers)
            resp = _get_ai_models(self.client, headers=self.headers)
            assert resp.status_code == 200, (
                f"[{spec.provider_id}] list-all expected 200, "
                f"got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            _assert_all_models_bucket_structure(body, label=spec.provider_id)
            assert "retrieved successfully" in body.get("message", "").lower(), body
            entry = _find_configured_model(
                body["models"][_MODEL_TYPE_LLM], created.model_key
            )
            assert entry is not None, (
                f"[{spec.provider_id}] modelKey {created.model_key!r} "
                f"not in models.llm: {body['models'][_MODEL_TYPE_LLM]}"
            )
            assert entry.get("provider") == created.provider
            # Stored KV entries omit modelType; bucket path implies llm.
        finally:
            if created:
                _teardown_provider(self.client, created)


# ==================================================================== #
# GET — models by type (/ai-models/{modelType})
# ==================================================================== #


@pytest.mark.integration
class TestGetModelsByType:
    """GET /configurationManager/ai-models/{modelType} — getModelsByType."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]
        self.timeout = providers_api["timeout"]

    def test_get_llm_empty_or_list_returns_200(self) -> None:
        resp = _get_models_by_type(
            self.client, _MODEL_TYPE_LLM, headers=self.headers
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("status") == "success", body
        assert isinstance(body.get("models"), list), body
        assert isinstance(body.get("message"), str) and body["message"], body

    @pytest.mark.parametrize(
        "label,model_type",
        [
            ("invalid modelType path", "not-a-real-type"),
        ],
    )
    def test_validation_rejects_invalid_model_type(
        self, label: str, model_type: str
    ) -> None:
        resp = _get_models_by_type(
            self.client, model_type, headers=self.headers
        )
        assert resp.status_code == 400, (
            f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        _assert_validation_error(body, label=label)
        _assert_error_envelope_matches_spec(body)

    def test_get_by_type_openapi_contract(self) -> None:
        resp = _get_models_by_type(
            self.client, _MODEL_TYPE_LLM, headers=self.headers
        )
        assert resp.status_code == 200, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getModelsByType", status_code="200"
        )

    def test_missing_authorization(self) -> None:
        resp = requests.get(
            _models_by_type_url(self.client, _MODEL_TYPE_LLM),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (400, 401), (
            f"Expected 400/401 without Authorization, got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 401:
            _assert_error_envelope_matches_spec(resp.json())


@pytest.mark.integration
class TestGetModelsByTypeNonAdmin:
    """GET /ai-models/{modelType} rejected for non-admin session JWT."""

    def test_non_admin_get_by_type_rejected(
        self, pipeshub_client: PipeshubClient
    ) -> None:
        if not os.getenv("PIPESHUB_TEST_NON_ADMIN_EMAIL") or not os.getenv(
            "PIPESHUB_TEST_NON_ADMIN_PASSWORD"
        ):
            pytest.skip(
                "Set PIPESHUB_TEST_NON_ADMIN_EMAIL and "
                "PIPESHUB_TEST_NON_ADMIN_PASSWORD to enable this test"
            )
        from auth_helpers import login_with_user, session_headers  # noqa: E402

        token, _ = login_with_user(
            pipeshub_client.base_url,
            os.environ["PIPESHUB_TEST_NON_ADMIN_EMAIL"].strip(),
            os.environ["PIPESHUB_TEST_NON_ADMIN_PASSWORD"].strip(),
            pipeshub_client.timeout_seconds,
        )
        resp = requests.get(
            _models_by_type_url(pipeshub_client, _MODEL_TYPE_LLM),
            headers=session_headers(token),
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"Expected 400 Admin access required, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("error", {}).get("message") == "Admin access required", body
        _assert_error_envelope_matches_spec(body)


@pytest.mark.integration
@pytest.mark.slow
class TestGetModelsByTypeLive:
    """POST provider then GET by type returns the created entry."""

    @pytest.fixture(autouse=True)
    def _setup(self, providers_api: Dict[str, Any]) -> None:
        self.client = providers_api["client"]
        self.headers = providers_api["headers"]

    @pytest.mark.parametrize("spec", _live_provider_specs(), ids=lambda s: s.provider_id)
    def test_get_llm_includes_created_provider(
        self, spec: LiveProviderSpec
    ) -> None:
        created: Optional[CreatedProvider] = None
        try:
            created = _create_live_provider(self.client, spec, headers=self.headers)
            resp = _get_models_by_type(
                self.client, _MODEL_TYPE_LLM, headers=self.headers
            )
            assert resp.status_code == 200, (
                f"[{spec.provider_id}] get-by-type expected 200, "
                f"got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("status") == "success", body
            assert f"{_MODEL_TYPE_LLM} models" in body.get("message", "").lower(), (
                body
            )
            entry = _find_configured_model(body["models"], created.model_key)
            assert entry is not None, (
                f"[{spec.provider_id}] modelKey {created.model_key!r} "
                f"not in models list: {body['models']}"
            )
            assert entry.get("provider") == created.provider
            assert_response_matches_openapi_operation(
                body, "getModelsByType", status_code="200"
            )
        finally:
            if created:
                _teardown_provider(self.client, created)
