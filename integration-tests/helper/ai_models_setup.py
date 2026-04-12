"""Helper for seeding (and tearing down) a test AI LLM model on PipeShub.

The indexing pipeline cannot finish (records will not reach ``indexingStatus =
COMPLETED``) unless an LLM is configured at the org level. This helper POSTs a
single OpenAI LLM through the same REST endpoint the frontend uses, captures
the ``modelKey`` from the response, and exposes a teardown helper that DELETEs
it again — so the test session leaves no residue on the backend.

Mirrors the frontend payload in
``frontend/src/sections/accountdetails/account-settings/ai-models/services/universal-config.ts``
(``modelService.addModel`` / ``modelService.deleteModel``).

Endpoints:
    POST   /api/v1/configurationManager/ai-models/providers
    DELETE /api/v1/configurationManager/ai-models/providers/{modelType}/{modelKey}

The model used is hard-coded for now — we only need one LLM to unblock the
indexing pipeline in tests:

    provider:  openAI
    modelType: llm
    model:     gpt-5.4-nano       (override with TEST_OPENAI_LLM_MODEL)

Env vars:
    TEST_OPENAI_API_KEY      – required; API key passed to OpenAI for the
                               backend's health-check before the model is
                               stored. Falls back to OPENAI_API_KEY.
    TEST_OPENAI_LLM_MODEL    – optional override for the model name
                               (default: "gpt-5.4-nano")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from pipeshub_client import PipeshubClient

logger = logging.getLogger("ai-models-setup")

_PROVIDERS_PATH = "/api/v1/configurationManager/ai-models/providers"

_DEFAULT_PROVIDER = "openAI"
_DEFAULT_MODEL_TYPE = "llm"
_DEFAULT_MODEL_NAME = "gpt-5.4-nano"


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


def _admin_headers(client: PipeshubClient) -> Dict[str, str]:
    client._ensure_access_token()
    return {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type": "application/json",
        "X-Is-Admin": "true",
    }


def setup_test_llm_model(client: PipeshubClient) -> SeededAIModel:
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
            "OPENAI_API_KEY) so the indexing pipeline tests can configure the "
            "LLM required for records to reach COMPLETED."
        )

    model_name = _model_name()
    payload: Dict[str, Any] = {
        "modelType": _DEFAULT_MODEL_TYPE,
        "provider": _DEFAULT_PROVIDER,
        "configuration": {
            "model": model_name,
            "apiKey": api_key,
        },
        "isMultimodal": False,
        "isReasoning": False,
        "isDefault": True,
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
            f"(provider={_DEFAULT_PROVIDER}, model={model_name}): "
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
        "Configured test LLM model: provider=%s model=%s modelKey=%s",
        _DEFAULT_PROVIDER, model_name, model_key,
    )
    return SeededAIModel(
        model_type=_DEFAULT_MODEL_TYPE,
        provider=_DEFAULT_PROVIDER,
        model_name=model_name,
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
