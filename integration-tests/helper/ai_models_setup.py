"""Helper for seeding (and tearing down) test AI models on PipeShub.

The indexing pipeline cannot finish (records will not reach ``indexingStatus =
COMPLETED``) unless both an LLM and a cloud embedding model are configured at
the org level. Without an embedding config the backend falls back to the local
CPU embedding server (``BAAI/bge-large-en-v1.5``), which is slow enough to
cause indexing timeouts in integration tests.

This helper POSTs LLM and embedding models through the same REST endpoint the
frontend uses, captures the ``modelKey`` from each response, and exposes
teardown helpers that DELETE them again — so the test session leaves no residue
on the backend.

Root ``ai_models_configured`` (``integration-tests/conftest.py``) seeds a
default non-reasoning LLM plus a default cloud embedding for indexing,
messaging, and connector ITs. Enterprise-search agent ITs seed a dedicated
reasoning model directly through this helper.

Mirrors the frontend payload in
``frontend/src/sections/accountdetails/account-settings/ai-models/services/universal-config.ts``
(``modelService.addModel`` / ``modelService.deleteModel``) and onboarding
``saveEmbeddingConfig`` in ``frontend/app/(main)/onboarding/api.ts``.

Endpoints:
    GET    /api/v1/configurationManager/ai-models/llm
    GET    /api/v1/configurationManager/ai-models/embedding
    POST   /api/v1/configurationManager/ai-models/providers
    DELETE /api/v1/configurationManager/ai-models/providers/{modelType}/{modelKey}

Env vars (first provider with complete credentials wins unless
``TEST_AI_MODEL_PROVIDER`` is set; on health-check failure the next configured
provider is tried):

    TEST_AI_MODEL_PROVIDER       – force provider id: openAI, azureOpenAI, gemini, groq

    OpenAI:
        TEST_OPENAI_API_KEY        – falls back to OPENAI_API_KEY
        TEST_OPENAI_LLM_MODEL      – default LLM (default: gpt-5.4-nano)
        TEST_OPENAI_EMBEDDING_MODEL – default embedding (default: text-embedding-3-small)

    Azure OpenAI:
        TEST_AZURE_OPENAI_API_KEY
        TEST_AZURE_OPENAI_ENDPOINT
        TEST_AZURE_OPENAI_DEPLOYMENT_NAME      – LLM deployment
        TEST_AZURE_OPENAI_MODEL
        TEST_AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME – optional; falls back to deployment name
        TEST_AZURE_OPENAI_EMBEDDING_MODEL      – optional (default: text-embedding-3-small)

    Gemini / Groq (LLM only):
        TEST_GEMINI_API_KEY, TEST_GEMINI_LLM_MODEL
        TEST_GROQ_API_KEY, TEST_GROQ_LLM_MODEL

Reasoning LLMs for agent ITs use provider-specific model names when no override
is passed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from pipeshub_client import PipeshubClient

logger = logging.getLogger("ai-models-setup")

_PROVIDERS_PATH = "/api/v1/configurationManager/ai-models/providers"
_LLM_BY_TYPE_PATH = "/api/v1/configurationManager/ai-models/llm"
_EMBEDDING_BY_TYPE_PATH = "/api/v1/configurationManager/ai-models/embedding"

_PROVIDER_OPENAI = "openAI"
_PROVIDER_AZURE_OPENAI = "azureOpenAI"
_PROVIDER_GEMINI = "gemini"
_PROVIDER_GROQ = "groq"

_DEFAULT_LLM_MODEL_TYPE = "llm"
_DEFAULT_EMBEDDING_MODEL_TYPE = "embedding"
_DEFAULT_MODEL_NAME = "gpt-5.4-nano"
_DEFAULT_REASONING_MODEL_NAME = "gpt-5.4-mini"
_DEFAULT_EMBEDDING_MODEL_NAME = "text-embedding-3-small"

_LLM_PROVIDER_ORDER = (
    _PROVIDER_AZURE_OPENAI,
    _PROVIDER_OPENAI,
    _PROVIDER_GEMINI,
    _PROVIDER_GROQ,
)
_EMBEDDING_PROVIDER_ORDER = (
    _PROVIDER_AZURE_OPENAI,
    _PROVIDER_OPENAI,
)


@dataclass
class SeededAIModel:
    """A model configured by a setup helper and pending teardown."""

    model_type: str
    provider: str
    model_name: str
    model_key: str


@dataclass
class SeededIndexingModels:
    """LLM + embedding pair seeded for the indexing pipeline."""

    llm: SeededAIModel
    embedding: SeededAIModel


@dataclass(frozen=True)
class _ProviderCandidate:
    """Resolved provider credentials ready for a providers POST."""

    provider: str
    model_name: str
    configuration: Dict[str, Any]


def _env(name: str, fallback: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, "").strip()
    if value:
        return value
    if fallback:
        fb = os.getenv(fallback, "").strip()
        if fb:
            return fb
    return None


def _forced_provider() -> Optional[str]:
    return _env("TEST_AI_MODEL_PROVIDER")


def _openai_api_key() -> Optional[str]:
    return _env("TEST_OPENAI_API_KEY", "OPENAI_API_KEY")


def _openai_llm_model_name(*, is_reasoning: bool) -> str:
    if is_reasoning:
        return _DEFAULT_REASONING_MODEL_NAME
    return _env("TEST_OPENAI_LLM_MODEL") or _DEFAULT_MODEL_NAME


def _openai_embedding_model_name() -> str:
    return _env("TEST_OPENAI_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODEL_NAME


def _azure_llm_model_name() -> str:
    return (
        _env("TEST_AZURE_OPENAI_MODEL")
        or _env("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
        or "gpt-4o"
    )


def _azure_embedding_model_name() -> str:
    return _env("TEST_AZURE_OPENAI_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODEL_NAME


def _azure_credentials_complete(*, for_embedding: bool) -> bool:
    if not _env("TEST_AZURE_OPENAI_API_KEY") or not _env("TEST_AZURE_OPENAI_ENDPOINT"):
        return False
    if for_embedding:
        deployment = (
            _env("TEST_AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
            or _env("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
        )
    else:
        deployment = _env("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
    return bool(deployment)


def _provider_credentials_available(provider: str, *, for_embedding: bool) -> bool:
    if provider == _PROVIDER_OPENAI:
        return bool(_openai_api_key())
    if provider == _PROVIDER_AZURE_OPENAI:
        return _azure_credentials_complete(for_embedding=for_embedding)
    if for_embedding:
        return False
    if provider == _PROVIDER_GEMINI:
        return bool(_env("TEST_GEMINI_API_KEY"))
    if provider == _PROVIDER_GROQ:
        return bool(_env("TEST_GROQ_API_KEY"))
    return False


def _provider_order(*, for_embedding: bool) -> List[str]:
    forced = _forced_provider()
    if forced:
        return [forced]
    base_order = _EMBEDDING_PROVIDER_ORDER if for_embedding else _LLM_PROVIDER_ORDER
    return [
        provider
        for provider in base_order
        if _provider_credentials_available(provider, for_embedding=for_embedding)
    ]


def _build_openai_configuration(api_key: str, model_name: str) -> Dict[str, Any]:
    return {"model": model_name, "apiKey": api_key}


def _build_azure_configuration(
    api_key: str,
    model_name: str,
    *,
    deployment_name: str,
) -> Dict[str, Any]:
    return {
        "endpoint": _env("TEST_AZURE_OPENAI_ENDPOINT") or "",
        "apiKey": api_key,
        "deploymentName": deployment_name,
        "model": model_name,
    }


def _build_llm_candidate(
    provider: str,
    *,
    is_reasoning: bool,
    model_name: str | None,
) -> Optional[_ProviderCandidate]:
    if provider == _PROVIDER_OPENAI:
        api_key = _openai_api_key()
        if not api_key:
            return None
        resolved_name = model_name or _openai_llm_model_name(is_reasoning=is_reasoning)
        return _ProviderCandidate(
            provider=provider,
            model_name=resolved_name,
            configuration=_build_openai_configuration(api_key, resolved_name),
        )

    if provider == _PROVIDER_AZURE_OPENAI:
        api_key = _env("TEST_AZURE_OPENAI_API_KEY")
        deployment = _env("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
        if not api_key or not deployment or not _env("TEST_AZURE_OPENAI_ENDPOINT"):
            return None
        resolved_name = model_name or _azure_llm_model_name()
        return _ProviderCandidate(
            provider=provider,
            model_name=resolved_name,
            configuration=_build_azure_configuration(
                api_key,
                resolved_name,
                deployment_name=deployment,
            ),
        )

    if provider == _PROVIDER_GEMINI:
        api_key = _env("TEST_GEMINI_API_KEY")
        if not api_key:
            return None
        resolved_name = model_name or _env("TEST_GEMINI_LLM_MODEL") or "gemini-2.5-flash"
        return _ProviderCandidate(
            provider=provider,
            model_name=resolved_name,
            configuration=_build_openai_configuration(api_key, resolved_name),
        )

    if provider == _PROVIDER_GROQ:
        api_key = _env("TEST_GROQ_API_KEY")
        if not api_key:
            return None
        resolved_name = model_name or _env("TEST_GROQ_LLM_MODEL") or "llama-3.3-70b-versatile"
        return _ProviderCandidate(
            provider=provider,
            model_name=resolved_name,
            configuration=_build_openai_configuration(api_key, resolved_name),
        )

    return None


def _build_embedding_candidate(
    provider: str,
    *,
    model_name: str | None,
) -> Optional[_ProviderCandidate]:
    if provider == _PROVIDER_OPENAI:
        api_key = _openai_api_key()
        if not api_key:
            return None
        resolved_name = model_name or _openai_embedding_model_name()
        return _ProviderCandidate(
            provider=provider,
            model_name=resolved_name,
            configuration=_build_openai_configuration(api_key, resolved_name),
        )

    if provider == _PROVIDER_AZURE_OPENAI:
        api_key = _env("TEST_AZURE_OPENAI_API_KEY")
        deployment = (
            _env("TEST_AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
            or _env("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
        )
        if not api_key or not deployment or not _env("TEST_AZURE_OPENAI_ENDPOINT"):
            return None
        resolved_name = model_name or _azure_embedding_model_name()
        return _ProviderCandidate(
            provider=provider,
            model_name=resolved_name,
            configuration=_build_azure_configuration(
                api_key,
                resolved_name,
                deployment_name=deployment,
            ),
        )

    return None


def _llm_provider_candidates(
    *,
    is_reasoning: bool,
    model_name: str | None,
) -> List[_ProviderCandidate]:
    candidates: List[_ProviderCandidate] = []
    for provider in _provider_order(for_embedding=False):
        candidate = _build_llm_candidate(
            provider,
            is_reasoning=is_reasoning,
            model_name=model_name,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _embedding_provider_candidates(
    *,
    model_name: str | None,
) -> List[_ProviderCandidate]:
    candidates: List[_ProviderCandidate] = []
    for provider in _provider_order(for_embedding=True):
        candidate = _build_embedding_candidate(provider, model_name=model_name)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _missing_credentials_message(*, for_embedding: bool) -> str:
    if for_embedding:
        return (
            "No embedding provider credentials found. Configure one of:\n"
            "  - OpenAI: TEST_OPENAI_API_KEY (or OPENAI_API_KEY)\n"
            "  - Azure OpenAI: TEST_AZURE_OPENAI_API_KEY, TEST_AZURE_OPENAI_ENDPOINT, "
            "and TEST_AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME "
            "(or TEST_AZURE_OPENAI_DEPLOYMENT_NAME)\n"
            "Or set TEST_AI_MODEL_PROVIDER to force a specific provider."
        )
    return (
        "No LLM provider credentials found. Configure one of:\n"
        "  - OpenAI: TEST_OPENAI_API_KEY (or OPENAI_API_KEY)\n"
        "  - Azure OpenAI: TEST_AZURE_OPENAI_API_KEY, TEST_AZURE_OPENAI_ENDPOINT, "
        "TEST_AZURE_OPENAI_DEPLOYMENT_NAME\n"
        "  - Gemini: TEST_GEMINI_API_KEY\n"
        "  - Groq: TEST_GROQ_API_KEY\n"
        "Or set TEST_AI_MODEL_PROVIDER to force a specific provider."
    )


def _admin_headers(client: PipeshubClient) -> Dict[str, str]:
    client._ensure_access_token()
    return {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type": "application/json",
        "X-Is-Admin": "true",
    }


def _as_dict(value: Any) -> Dict[str, Any]:
    """Coerce API/entry JSON values to a dict; non-dicts become empty."""
    return value if isinstance(value, dict) else {}


def _parse_model_name_from_config(entry: Dict[str, Any]) -> str:
    configuration = _as_dict(entry.get("configuration"))
    raw = configuration.get("model") or entry.get("modelName") or ""
    if isinstance(raw, str) and "," in raw:
        return raw.split(",")[0].strip()
    return str(raw).strip() if raw else ""


def list_configured_llm_models(client: PipeshubClient) -> List[Dict[str, Any]]:
    """Return all org-configured LLM entries from Configuration Manager."""
    url = client._url(_LLM_BY_TYPE_PATH)
    resp = requests.get(
        url,
        headers=_admin_headers(client),
        timeout=client.timeout_seconds,
    )
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Failed to list LLM models: HTTP {resp.status_code} {resp.text[:500]}"
        )
    data = _as_dict(resp.json())
    models = data.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def seeded_model_from_config(entry: Dict[str, Any]) -> SeededAIModel:
    """Build ``SeededAIModel`` from a Configuration Manager list entry."""
    model_key = entry.get("modelKey")
    if not isinstance(model_key, str) or not model_key:
        raise RuntimeError(f"LLM config missing modelKey: {entry!r}")
    provider = str(entry.get("provider") or _PROVIDER_OPENAI)
    model_name = _parse_model_name_from_config(entry) or model_key
    return SeededAIModel(
        model_type=_DEFAULT_LLM_MODEL_TYPE,
        provider=provider,
        model_name=model_name,
        model_key=model_key,
    )


def _post_provider_model(
    client: PipeshubClient,
    *,
    candidate: _ProviderCandidate,
    model_type: str,
    is_reasoning: bool,
    is_multimodal: bool,
    is_default: bool,
) -> SeededAIModel:
    payload: Dict[str, Any] = {
        "modelType": model_type,
        "provider": candidate.provider,
        "configuration": candidate.configuration,
        "isMultimodal": is_multimodal,
        "isReasoning": is_reasoning,
        "isDefault": is_default,
        "contextLength": None,
    }

    url = client._url(_PROVIDERS_PATH)
    resp = requests.post(
        url,
        headers=_admin_headers(client),
        json=payload,
        timeout=client.timeout_seconds,
    )

    if resp.status_code >= 300:
        body = ""
        try:
            body = resp.text or ""
        except Exception:
            pass
        raise RuntimeError(
            f"Failed to configure test {model_type} model "
            f"(provider={candidate.provider}, model={candidate.model_name}, "
            f"isReasoning={is_reasoning}, isMultimodal={is_multimodal}): "
            f"HTTP {resp.status_code} {body[:500]}"
        )

    try:
        data = _as_dict(resp.json())
    except ValueError as e:
        raise RuntimeError(
            f"{model_type} model add returned non-JSON body: {e}"
        ) from e

    details = _as_dict(data.get("details"))
    model_key = details.get("modelKey")
    if not model_key:
        raise RuntimeError(
            f"{model_type} model add response missing details.modelKey: {data}"
        )

    logger.info(
        "Configured test %s model: provider=%s model=%s modelKey=%s "
        "isReasoning=%s isMultimodal=%s",
        model_type,
        candidate.provider,
        candidate.model_name,
        model_key,
        is_reasoning,
        is_multimodal,
    )
    return SeededAIModel(
        model_type=model_type,
        provider=candidate.provider,
        model_name=candidate.model_name,
        model_key=model_key,
    )


def _seed_model_with_fallback(
    client: PipeshubClient,
    *,
    candidates: List[_ProviderCandidate],
    model_type: str,
    is_reasoning: bool,
    is_multimodal: bool,
    is_default: bool,
    missing_credentials_message: str,
) -> SeededAIModel:
    if not candidates:
        raise RuntimeError(missing_credentials_message)

    errors: List[str] = []
    for candidate in candidates:
        try:
            return _post_provider_model(
                client,
                candidate=candidate,
                model_type=model_type,
                is_reasoning=is_reasoning,
                is_multimodal=is_multimodal,
                is_default=is_default,
            )
        except RuntimeError as exc:
            logger.warning(
                "Test %s provider %s failed health check / configure: %s",
                model_type,
                candidate.provider,
                exc,
            )
            errors.append(f"{candidate.provider}: {exc}")

    raise RuntimeError(
        f"All configured {model_type} providers failed. Tried: "
        + "; ".join(errors)
    )


def setup_test_llm_model(
    client: PipeshubClient,
    *,
    is_reasoning: bool = False,
    is_multimodal: bool = False,
    is_default: bool = True,
    model_name: str | None = None,
) -> SeededAIModel:
    """Add a single LLM model and return its assigned modelKey.

    Tries providers in env order (OpenAI, Azure OpenAI, Gemini, Groq) until one
    passes the backend health check. Set ``TEST_AI_MODEL_PROVIDER`` to force one.

    Raises ``RuntimeError`` if no provider credentials are available or all fail.
    """
    candidates = _llm_provider_candidates(
        is_reasoning=is_reasoning,
        model_name=model_name,
    )
    return _seed_model_with_fallback(
        client,
        candidates=candidates,
        model_type=_DEFAULT_LLM_MODEL_TYPE,
        is_reasoning=is_reasoning,
        is_multimodal=is_multimodal,
        is_default=is_default,
        missing_credentials_message=_missing_credentials_message(for_embedding=False),
    )


def setup_test_embedding_model(
    client: PipeshubClient,
    *,
    is_multimodal: bool = False,
    is_default: bool = True,
    model_name: str | None = None,
) -> SeededAIModel:
    """Add a single embedding model and return its assigned modelKey.

    Tries OpenAI then Azure OpenAI when credentials are present.

    Raises ``RuntimeError`` if no provider credentials are available or all fail.
    """
    candidates = _embedding_provider_candidates(model_name=model_name)
    return _seed_model_with_fallback(
        client,
        candidates=candidates,
        model_type=_DEFAULT_EMBEDDING_MODEL_TYPE,
        is_reasoning=False,
        is_multimodal=is_multimodal,
        is_default=is_default,
        missing_credentials_message=_missing_credentials_message(for_embedding=True),
    )


def setup_test_indexing_models(client: PipeshubClient) -> SeededIndexingModels:
    """Seed default org LLM + cloud embedding for indexing-heavy integration tests.

    The indexing service reads both from org AI model config (KV store), not from
    test-local variables — these helpers only POST/DELETE via the Configuration
    Manager API.
    """
    return SeededIndexingModels(
        llm=setup_test_llm_model(client),
        embedding=setup_test_embedding_model(client),
    )


def teardown_test_indexing_models(
    client: PipeshubClient,
    models: SeededIndexingModels,
) -> None:
    """DELETE LLM and embedding models seeded by ``setup_test_indexing_models``."""
    teardown_test_embedding_model(client, models.embedding)
    teardown_test_llm_model(client, models.llm)


def teardown_test_llm_model(
    client: PipeshubClient,
    seeded: SeededAIModel,
) -> None:
    """DELETE a previously seeded model. Logs (does not raise) on failure so
    teardown never masks the real test outcome."""
    url = client._url(
        f"{_PROVIDERS_PATH}/{seeded.model_type}/{seeded.model_key}"
    )
    try:
        resp = requests.delete(
            url,
            headers=_admin_headers(client),
            timeout=client.timeout_seconds,
        )
    except Exception as e:
        logger.warning(
            "Error deleting test %s model %s: %s",
            seeded.model_type, seeded.model_key, e,
        )
        return

    if resp.status_code >= 300:
        body = ""
        try:
            body = resp.text or ""
        except Exception:
            pass
        logger.warning(
            "Failed to delete test %s model modelKey=%s: HTTP %d %s",
            seeded.model_type, seeded.model_key, resp.status_code, body[:300],
        )
        return

    logger.info(
        "Deleted test %s model: provider=%s model=%s modelKey=%s",
        seeded.model_type, seeded.provider, seeded.model_name, seeded.model_key,
    )


def teardown_test_embedding_model(
    client: PipeshubClient,
    seeded: SeededAIModel,
) -> None:
    """DELETE a previously seeded embedding model."""
    teardown_test_llm_model(client, seeded)
