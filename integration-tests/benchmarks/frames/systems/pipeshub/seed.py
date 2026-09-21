"""Registering the benchmark's models in PipesHub when they are missing.

Model identity lives in PipesHub's registry; keys come from the environment.
Non-reasoning models are registered at temperature 0 (PipesHub defaults to
0.2); reasoning models are pinned to 1 by the provider, so runs use repeats.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ai_models_setup import seed_explicit_llm, setup_test_embedding_model
from benchmarks.frames.config import EmbeddingSelector, ModelSelector
from benchmarks.frames.credentials import Credentials
from benchmarks.frames.errors import ConfigError
from benchmarks.frames.llm.client import ResolvedModel
from benchmarks.frames.llm.providers import provider_spec
from benchmarks.frames.llm.registry import ModelResolver
from benchmarks.frames.systems.pipeshub.session import UserSession

logger = logging.getLogger(__name__)

_EMBEDDING_LIST_PATH = "/api/v1/configurationManager/ai-models/embedding"


def _has_embedding_model(session: UserSession) -> bool:
    resp = session.request("GET", _EMBEDDING_LIST_PATH)
    return resp.status_code < 400 and bool((resp.json() or {}).get("models"))


def verify_indexing_embedding(session: UserSession, selector: EmbeddingSelector) -> None:
    """Baselines must embed queries with the model PipesHub indexed with."""
    resp = session.request("GET", _EMBEDDING_LIST_PATH)
    models = (resp.json() or {}).get("models") or [] if resp.status_code < 400 else []
    default = next((m for m in models if m.get("isDefault")), models[0] if models else None)
    if default is None:
        raise ConfigError("PipesHub has no embedding model configured")
    configured = ((default.get("configuration") or {}).get("model") or "").split(",")[0].strip()
    expected_provider = selector.pipeshub_provider or selector.provider
    if default.get("provider") != expected_provider or configured != selector.model:
        raise ConfigError(
            f"config embedding {expected_provider}:{selector.model} != PipesHub's "
            f"{default.get('provider')}:{configured}; RAG baselines would search a different vector space",
        )


def ensure_models(
    session: UserSession,
    resolver: ModelResolver,
    selectors: Sequence[ModelSelector],
    credentials: Credentials,
    *,
    default_selector: ModelSelector,
) -> list[ResolvedModel]:
    for selector in dict.fromkeys(selectors):
        if resolver.find(selector) is not None:
            continue
        if not selector.provider:
            raise ConfigError(f"model {selector.model!r} is not registered and has no provider to seed it with")
        seeded = seed_explicit_llm(
            session,
            provider=selector.provider,
            model_name=selector.model,
            api_key=credentials.first_of(provider_spec(selector.provider).key_envs),
            is_reasoning=selector.is_reasoning,
            is_default=selector == default_selector,
            extra_configuration=None if selector.is_reasoning else {"temperature": 0},
        )
        logger.info("registered %s:%s as %s", seeded.provider, seeded.model_name, seeded.model_key)
        resolver.refresh()
    if not _has_embedding_model(session):
        embedding = setup_test_embedding_model(session, is_default=True)
        logger.info("registered embedding model %s", embedding.model_name)
    return [resolver.resolve(selector) for selector in selectors]
