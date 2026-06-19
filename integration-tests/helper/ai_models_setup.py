"""Helper for seeding (and tearing down) test AI models on PipeShub.

The indexing pipeline cannot finish (records will not reach ``indexingStatus =
COMPLETED``) unless both an LLM and a cloud embedding model are configured at
the org level. Without an embedding config the backend falls back to the local
CPU embedding server (``BAAI/bge-large-en-v1.5``), which is slow enough to
cause indexing timeouts in integration tests.

This helper POSTs OpenAI LLM and embedding models through the same REST
endpoint the frontend uses, captures the ``modelKey`` from each response, and
exposes teardown helpers that DELETE them again — so the test session leaves no
residue on the backend.

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

Env vars:
    TEST_OPENAI_API_KEY        – required for seeding; falls back to OPENAI_API_KEY
    TEST_OPENAI_LLM_MODEL      – default LLM name for indexing (default: "gpt-5.4-nano")
    TEST_OPENAI_EMBEDDING_MODEL – default embedding name (default: "text-embedding-3-small")

Reasoning LLMs for agent ITs are seeded with a fixed model name (``gpt-5.4-mini``)
when no org LLM with ``isReasoning: true`` exists.
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

_DEFAULT_PROVIDER = "openAI"
_DEFAULT_LLM_MODEL_TYPE = "llm"
_DEFAULT_EMBEDDING_MODEL_TYPE = "embedding"
_DEFAULT_MODEL_NAME = "gpt-5.4-nano"
_DEFAULT_REASONING_MODEL_NAME = "gpt-5.4-mini"
_DEFAULT_EMBEDDING_MODEL_NAME = "text-embedding-3-small"


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


def _api_key() -> Optional[str]:
    return (
        os.getenv("TEST_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or None
    )


def _model_name() -> str:
    return os.getenv("TEST_OPENAI_LLM_MODEL", _DEFAULT_MODEL_NAME).strip() or _DEFAULT_MODEL_NAME


def _reasoning_model_name() -> str:
    return _DEFAULT_REASONING_MODEL_NAME


def _embedding_model_name() -> str:
    return (
        os.getenv("TEST_OPENAI_EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL_NAME).strip()
        or _DEFAULT_EMBEDDING_MODEL_NAME
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
    provider = str(entry.get("provider") or _DEFAULT_PROVIDER)
    model_name = _parse_model_name_from_config(entry) or model_key
    return SeededAIModel(
        model_type=_DEFAULT_LLM_MODEL_TYPE,
        provider=provider,
        model_name=model_name,
        model_key=model_key,
    )


def setup_test_llm_model(
    client: PipeshubClient,
    *,
    is_reasoning: bool = False,
    is_multimodal: bool = False,
    is_default: bool = True,
    model_name: str | None = None,
) -> SeededAIModel:
    """Add a single OpenAI LLM model and return its assigned modelKey.

    Mirrors the frontend ``modelService.addModel`` payload exactly so the same
    backend validation / health-check path runs in tests.

    Raises ``RuntimeError`` if no API key is set, or if the backend rejects the
    model (e.g. health check fails).
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError(
            "No OpenAI API key found in env. Set TEST_OPENAI_API_KEY (or "
            "OPENAI_API_KEY) so integration tests can configure the LLM."
        )

    resolved_name = model_name
    if not resolved_name:
        resolved_name = _reasoning_model_name() if is_reasoning else _model_name()

    payload: Dict[str, Any] = {
        "modelType": _DEFAULT_LLM_MODEL_TYPE,
        "provider": _DEFAULT_PROVIDER,
        "configuration": {
            "model": resolved_name,
            "apiKey": api_key,
        },
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
            f"Failed to configure test LLM model "
            f"(provider={_DEFAULT_PROVIDER}, model={resolved_name}, "
            f"isReasoning={is_reasoning}, isMultimodal={is_multimodal}): "
            f"HTTP {resp.status_code} {body[:500]}"
        )

    try:
        data = _as_dict(resp.json())
    except ValueError as e:
        raise RuntimeError(
            f"LLM model add returned non-JSON body: {e}"
        ) from e

    details = _as_dict(data.get("details"))
    model_key = details.get("modelKey")
    if not model_key:
        raise RuntimeError(
            f"LLM model add response missing details.modelKey: {data}"
        )

    logger.info(
        "Configured test LLM model: provider=%s model=%s modelKey=%s isReasoning=%s isMultimodal=%s",
        _DEFAULT_PROVIDER, resolved_name, model_key, is_reasoning, is_multimodal,
    )
    return SeededAIModel(
        model_type=_DEFAULT_LLM_MODEL_TYPE,
        provider=_DEFAULT_PROVIDER,
        model_name=resolved_name,
        model_key=model_key,
    )


def setup_test_embedding_model(
    client: PipeshubClient,
    *,
    is_multimodal: bool = False,
    is_default: bool = True,
    model_name: str | None = None,
) -> SeededAIModel:
    """Add a single OpenAI embedding model and return its assigned modelKey.

    Mirrors the frontend onboarding ``saveEmbeddingConfig`` payload so the same
    backend validation / health-check path runs in tests.

    Raises ``RuntimeError`` if no API key is set, or if the backend rejects the
    model (e.g. health check fails).
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError(
            "No OpenAI API key found in env. Set TEST_OPENAI_API_KEY (or "
            "OPENAI_API_KEY) so integration tests can configure the embedding model."
        )

    resolved_name = model_name or _embedding_model_name()

    payload: Dict[str, Any] = {
        "modelType": _DEFAULT_EMBEDDING_MODEL_TYPE,
        "provider": _DEFAULT_PROVIDER,
        "configuration": {
            "model": resolved_name,
            "apiKey": api_key,
        },
        "isMultimodal": is_multimodal,
        "isReasoning": False,
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
            f"Failed to configure test embedding model "
            f"(provider={_DEFAULT_PROVIDER}, model={resolved_name}, "
            f"isMultimodal={is_multimodal}): "
            f"HTTP {resp.status_code} {body[:500]}"
        )

    try:
        data = _as_dict(resp.json())
    except ValueError as e:
        raise RuntimeError(
            f"Embedding model add returned non-JSON body: {e}"
        ) from e

    details = _as_dict(data.get("details"))
    model_key = details.get("modelKey")
    if not model_key:
        raise RuntimeError(
            f"Embedding model add response missing details.modelKey: {data}"
        )

    logger.info(
        "Configured test embedding model: provider=%s model=%s modelKey=%s isMultimodal=%s",
        _DEFAULT_PROVIDER, resolved_name, model_key, is_multimodal,
    )
    return SeededAIModel(
        model_type=_DEFAULT_EMBEDDING_MODEL_TYPE,
        provider=_DEFAULT_PROVIDER,
        model_name=resolved_name,
        model_key=model_key,
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
