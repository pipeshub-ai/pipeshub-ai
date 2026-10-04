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

    Ollama (no key; tried only when TEST_OLLAMA_ENDPOINT is set):
        TEST_OLLAMA_ENDPOINT          – as the stack reaches it, e.g. http://ollama:11434
        TEST_OLLAMA_LLM_MODEL         – default: qwen2.5:0.5b
        TEST_OLLAMA_EMBEDDING_MODEL   – default: nomic-embed-text

Reasoning LLMs for agent ITs use provider-specific model names when no override
is passed. On OpenAI that is gpt-5.6-luna, and no env var overrides it —
TEST_OPENAI_LLM_MODEL applies only to the non-reasoning default.
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
_PROVIDER_OLLAMA = "ollama"

_DEFAULT_LLM_MODEL_TYPE = "llm"
_DEFAULT_EMBEDDING_MODEL_TYPE = "embedding"
_DEFAULT_MODEL_NAME = "gpt-5.4-nano"
_DEFAULT_REASONING_MODEL_NAME = "gpt-5.6-luna"
_DEFAULT_EMBEDDING_MODEL_NAME = "text-embedding-3-small"
# The model PipesHub embeds with when the org has configured none.
_BUILT_IN_EMBEDDING_MODEL_NAME = "BAAI/bge-large-en-v1.5"
# Part of the product's refusal to change or delete the embedding model while
# the vector store holds vectors from it (cm_controller.ts
# EMBEDDING_MODEL_IN_USE_MESSAGE).
_EMBEDDING_IN_USE_MARKER = "is embedding your indexed content"

_LLM_PROVIDER_ORDER = (
    _PROVIDER_AZURE_OPENAI,
    _PROVIDER_OPENAI,
    _PROVIDER_GEMINI,
    _PROVIDER_GROQ,
    _PROVIDER_OLLAMA,
)
_EMBEDDING_PROVIDER_ORDER = (
    _PROVIDER_AZURE_OPENAI,
    _PROVIDER_OPENAI,
    _PROVIDER_OLLAMA,
)


@dataclass
class SeededAIModel:
    """A model configured by a setup helper and pending teardown.

    ``owned`` is False for a model the org already had, which teardown leaves.
    """

    model_type: str
    provider: str
    model_name: str
    model_key: str
    owned: bool = True


@dataclass
class SeededIndexingModels:
    """LLM + embedding pair seeded for the indexing pipeline."""

    llm: SeededAIModel
    embedding: SeededAIModel


class _ModelRefused(RuntimeError):
    """The providers POST answered with an error status."""

    def __init__(self, message: str, *, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


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
    if provider == _PROVIDER_OLLAMA:
        return bool(_env("TEST_OLLAMA_ENDPOINT"))
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


def _build_ollama_configuration(model_name: str) -> Optional[Dict[str, Any]]:
    endpoint = _env("TEST_OLLAMA_ENDPOINT")
    if not endpoint:
        return None
    return {"model": model_name, "endpoint": endpoint}


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

    if provider == _PROVIDER_OLLAMA:
        resolved_name = model_name or _env("TEST_OLLAMA_LLM_MODEL") or "qwen2.5:0.5b"
        configuration = _build_ollama_configuration(resolved_name)
        if configuration is None:
            return None
        return _ProviderCandidate(provider=provider, model_name=resolved_name, configuration=configuration)

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

    if provider == _PROVIDER_OLLAMA:
        resolved_name = model_name or _env("TEST_OLLAMA_EMBEDDING_MODEL") or "nomic-embed-text"
        configuration = _build_ollama_configuration(resolved_name)
        if configuration is None:
            return None
        return _ProviderCandidate(provider=provider, model_name=resolved_name, configuration=configuration)

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


def llm_configuration(
    *,
    is_reasoning: bool = False,
    model_name: str | None = None,
) -> Optional[tuple[str, str, Dict[str, Any]]]:
    """``(provider, model_name, configuration)`` for the first LLM provider with
    credentials, as a providers POST sends it; ``None`` when there is none.

    For tests that add a model themselves (a friendly name, a bad key) rather
    than through :func:`setup_test_llm_model`.
    """
    candidates = _llm_provider_candidates(is_reasoning=is_reasoning, model_name=model_name)
    if not candidates:
        return None
    first = candidates[0]
    return first.provider, first.model_name, dict(first.configuration)


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


def _list_configured_models(
    client: PipeshubClient, path: str, kind: str
) -> List[Dict[str, Any]]:
    resp = requests.get(
        client._url(path),
        headers=_admin_headers(client),
        timeout=client.timeout_seconds,
    )
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Failed to list {kind} models: HTTP {resp.status_code} {resp.text[:500]}"
        )
    data = _as_dict(resp.json())
    models = data.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def list_configured_llm_models(client: PipeshubClient) -> List[Dict[str, Any]]:
    """Return all org-configured LLM entries from Configuration Manager."""
    return _list_configured_models(client, _LLM_BY_TYPE_PATH, "LLM")


def list_configured_embedding_models(client: PipeshubClient) -> List[Dict[str, Any]]:
    """Return all org-configured embedding entries from Configuration Manager."""
    return _list_configured_models(client, _EMBEDDING_BY_TYPE_PATH, "embedding")


def _active_embedding_entry(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The entry the services embed with: the default one, else the first."""
    return next((e for e in entries if e.get("isDefault")), entries[0] if entries else None)


def _same_setting(a: Any, b: Any) -> bool:
    return str(a).strip().rstrip("/").lower() == str(b).strip().rstrip("/").lower()


def _entry_matches(entry: Dict[str, Any], candidate: _ProviderCandidate) -> bool:
    """Whether ``entry`` is the model ``candidate`` would add.

    The list response strips secrets, so the key is not compared; the endpoint
    and deployment are, when the entry still carries them.
    """
    if entry.get("provider") != candidate.provider:
        return False
    if not _same_setting(_parse_model_name_from_config(entry), candidate.model_name):
        return False
    configuration = _as_dict(entry.get("configuration"))
    for field in ("endpoint", "deploymentName"):
        wanted = candidate.configuration.get(field)
        have = configuration.get(field)
        if wanted and have and not _same_setting(wanted, have):
            return False
    return True


def _describe_entry(entry: Optional[Dict[str, Any]]) -> str:
    if entry is None:
        return f"the built-in model ({_BUILT_IN_EMBEDDING_MODEL_NAME})"
    model = _parse_model_name_from_config(entry) or entry.get("modelKey") or "?"
    return f"{entry.get('provider')} {model} (modelKey={entry.get('modelKey')})"


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
        raise _ModelRefused(
            f"Failed to configure test {model_type} model "
            f"(provider={candidate.provider}, model={candidate.model_name}, "
            f"isReasoning={is_reasoning}, isMultimodal={is_multimodal}): "
            f"HTTP {resp.status_code} {body[:500]}",
            status_code=resp.status_code,
            body=body,
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
            if isinstance(exc, _ModelRefused) and _EMBEDDING_IN_USE_MARKER in exc.body:
                # The refusal is about what the vector store holds, so every
                # other provider would get the same answer.
                raise
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
    """Make the wanted embedding model the one the org embeds with.

    When the org already embeds with it (same provider and model), that entry is
    reused as it is and teardown leaves it: the stack is shared by every pytest
    session and the Playwright run, and PipesHub refuses to delete or replace
    the embedding model while the vector store holds its vectors. Otherwise the
    model is added, trying Azure OpenAI, OpenAI, then Ollama when credentials
    are present.

    Raises ``RuntimeError`` if no provider credentials are available, all fail,
    or another model has already embedded content. The suite does not delete
    those embeddings itself: Labs' cleanup empties the vector store for every
    org on the deployment, and refuses while anything is indexing.
    """
    candidates = _embedding_provider_candidates(model_name=model_name)
    active = _active_embedding_entry(list_configured_embedding_models(client))
    if active is not None and isinstance(active.get("modelKey"), str):
        for candidate in candidates:
            if _entry_matches(active, candidate):
                logger.info(
                    "Reusing the org's embedding model: provider=%s model=%s modelKey=%s",
                    candidate.provider,
                    candidate.model_name,
                    active["modelKey"],
                )
                return SeededAIModel(
                    model_type=_DEFAULT_EMBEDDING_MODEL_TYPE,
                    provider=candidate.provider,
                    model_name=candidate.model_name,
                    model_key=active["modelKey"],
                    owned=False,
                )
    try:
        return _seed_model_with_fallback(
            client,
            candidates=candidates,
            model_type=_DEFAULT_EMBEDDING_MODEL_TYPE,
            is_reasoning=False,
            is_multimodal=is_multimodal,
            is_default=is_default,
            missing_credentials_message=_missing_credentials_message(for_embedding=True),
        )
    except _ModelRefused as exc:
        if _EMBEDDING_IN_USE_MARKER not in exc.body:
            raise
        wanted = ", ".join(f"{c.provider} {c.model_name}" for c in candidates)
        raise RuntimeError(
            f"Cannot configure the test embedding model ({wanted}): this "
            f"deployment already embeds content with {_describe_entry(active)}, "
            "and PipesHub refuses to switch models while those vectors are "
            "stored. Run the suite on a fresh stack, configure the test model "
            "before anything is indexed, or delete the embeddings in Labs "
            "(Workspace > Labs > Vector Store) and run it again. "
            f"PipesHub answered: HTTP {exc.status_code} {exc.body[:300]}"
        ) from exc


def setup_test_indexing_models(client: PipeshubClient) -> SeededIndexingModels:
    """Seed default org LLM + cloud embedding for indexing-heavy integration tests.

    The indexing service reads both from org AI model config (KV store), not from
    test-local variables — these helpers only POST/DELETE via the Configuration
    Manager API.
    """
    llm = setup_test_llm_model(client)
    try:
        embedding = setup_test_embedding_model(client)
    except Exception:
        # The LLM is already in the org's config at this point; leaving it there
        # would change what every later test and benchmark runs against.
        teardown_test_llm_model(client, llm)
        raise
    return SeededIndexingModels(llm=llm, embedding=embedding)


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
    teardown never masks the real test outcome. A model the org already had
    (``owned`` False) is left alone."""
    if not seeded.owned:
        logger.info(
            "Leaving the org's %s model in place: provider=%s model=%s modelKey=%s",
            seeded.model_type, seeded.provider, seeded.model_name, seeded.model_key,
        )
        return
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
        if _EMBEDDING_IN_USE_MARKER in body:
            logger.info(
                "Left the test %s model modelKey=%s configured: its vectors are "
                "stored, so PipesHub refuses to delete it. The next session on "
                "this stack reuses it.",
                seeded.model_type, seeded.model_key,
            )
            return
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
