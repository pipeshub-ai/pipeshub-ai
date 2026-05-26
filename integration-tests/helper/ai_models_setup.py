"""Helper for seeding (and tearing down) a test AI LLM model on PipeShub.

The indexing pipeline cannot finish (records will not reach ``indexingStatus =
COMPLETED``) unless an LLM is configured at the org level. This helper POSTs a
single OpenAI LLM through the same REST endpoint the frontend uses, captures
the ``modelKey`` from the response, and exposes a teardown helper that DELETEs
it again — so the test session leaves no residue on the backend.

Root ``ai_models_configured`` (``integration-tests/conftest.py``) seeds a
default non-reasoning LLM for indexing and messaging. Enterprise-search agent
ITs use ``reasoning_llm_model`` (``enterprise_search/conftest.py``), which
lists org LLMs and picks or seeds one with ``isReasoning: true``.

Mirrors the frontend payload in
``frontend/src/sections/accountdetails/account-settings/ai-models/services/universal-config.ts``
(``modelService.addModel`` / ``modelService.deleteModel``).

Endpoints:
    GET    /api/v1/configurationManager/ai-models/llm
    POST   /api/v1/configurationManager/ai-models/providers
    DELETE /api/v1/configurationManager/ai-models/providers/{modelType}/{modelKey}

Env vars:
    TEST_OPENAI_API_KEY   – required for seeding; falls back to OPENAI_API_KEY
    TEST_OPENAI_LLM_MODEL – default LLM name for indexing (default: "gpt-5.4-nano")

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

_DEFAULT_PROVIDER = "openAI"
_DEFAULT_MODEL_TYPE = "llm"
_DEFAULT_MODEL_NAME = "gpt-5.4-nano"
_DEFAULT_REASONING_MODEL_NAME = "gpt-5.4-mini"


@dataclass
class SeededAIModel:
    """A model configured by ``setup_test_llm_model`` and pending teardown."""

    model_type: str
    provider: str
    model_name: str
    model_key: str


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


def _admin_headers(client: PipeshubClient) -> Dict[str, str]:
    client._ensure_access_token()
    return {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type": "application/json",
        "X-Is-Admin": "true",
    }


def _parse_model_name_from_config(entry: Dict[str, Any]) -> str:
    configuration = entry.get("configuration") or {}
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
    data = resp.json() or {}
    models = data.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def pick_reasoning_llm_model(models: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """First configured LLM with ``isReasoning`` true, or None."""
    for entry in models:
        if entry.get("isReasoning") is True:
            return entry
    return None


def seeded_model_from_config(entry: Dict[str, Any]) -> SeededAIModel:
    """Build ``SeededAIModel`` from a Configuration Manager list entry."""
    model_key = entry.get("modelKey")
    if not isinstance(model_key, str) or not model_key:
        raise RuntimeError(f"LLM config missing modelKey: {entry!r}")
    provider = str(entry.get("provider") or _DEFAULT_PROVIDER)
    model_name = _parse_model_name_from_config(entry) or model_key
    return SeededAIModel(
        model_type=_DEFAULT_MODEL_TYPE,
        provider=provider,
        model_name=model_name,
        model_key=model_key,
    )


def setup_test_llm_model(
    client: PipeshubClient,
    *,
    is_reasoning: bool = False,
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
        "modelType": _DEFAULT_MODEL_TYPE,
        "provider": _DEFAULT_PROVIDER,
        "configuration": {
            "model": resolved_name,
            "apiKey": api_key,
        },
        "isMultimodal": False,
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
            f"isReasoning={is_reasoning}): "
            f"HTTP {resp.status_code} {body[:500]}"
        )

    try:
        data = resp.json() or {}
    except ValueError as e:
        raise RuntimeError(
            f"LLM model add returned non-JSON body: {e}"
        ) from e

    details = data.get("details") or {}
    model_key = details.get("modelKey")
    if not model_key:
        raise RuntimeError(
            f"LLM model add response missing details.modelKey: {data}"
        )

    logger.info(
        "Configured test LLM model: provider=%s model=%s modelKey=%s isReasoning=%s",
        _DEFAULT_PROVIDER, resolved_name, model_key, is_reasoning,
    )
    return SeededAIModel(
        model_type=_DEFAULT_MODEL_TYPE,
        provider=_DEFAULT_PROVIDER,
        model_name=resolved_name,
        model_key=model_key,
    )


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
            "Error deleting test LLM model %s: %s",
            seeded.model_key, e,
        )
        return

    if resp.status_code >= 300:
        body = ""
        try:
            body = resp.text or ""
        except Exception:
            pass
        logger.warning(
            "Failed to delete test LLM model modelKey=%s: HTTP %d %s",
            seeded.model_key, resp.status_code, body[:300],
        )
        return

    logger.info(
        "Deleted test LLM model: provider=%s model=%s modelKey=%s",
        seeded.provider, seeded.model_name, seeded.model_key,
    )
