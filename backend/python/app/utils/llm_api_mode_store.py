"""Learned per-model LLM API-mode facts.

`LangChainTransport` (see `app/agents/agent_loop/langchain_transport.py`)
retries once against a different reasoning configuration when a provider
rejects the one it was sent — either the wrong OpenAI API shape (Chat
Completions vs. Responses) for reasoning + bound function tools, or a
gateway/model that refuses to have reasoning disabled at all. This covers
gateways (Requesty, OpenRouter, LiteLLM proxy, ...) that alias arbitrary
backing models under names our upfront heuristic in `app/utils/aimodels.py`
can't reliably classify.

That retry is a real request each time unless the outcome is remembered.
This module stores the learned outcome in a small KV entry, so a given
(model_key, model_name) pair pays the failed round-trip at most once per
`_LEARNED_FACT_TTL_SECONDS` window, and is then applied directly by
`aimodels._reasoning_effort_kwargs()` for every subsequent call.

Deliberately NOT stored inside `/services/aiModels` (`config_node_constants
.AI_MODELS`): that blob is owned by the Node.js admin API, which
read-modify-writes it on every provider add/update/delete with no
compare-and-set (`cm_controller.ts`), and holds provider API keys — a race
between an admin save and a learned-fact write there would silently drop
one of the two, and losing an admin's credential edit is not an acceptable
price for a cache line. A private sibling key has no such risk: nothing
else reads or writes it, so at worst two query-service pods racing each
other on the same fact overwrite one another (harmless — both writes agree
on which mode works, or the loser simply gets corrected on its own next
failed call).
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = create_logger("llm_api_mode_store")

# Long enough that a hot agent loop never re-probes a model it already
# talked to successfully this week; short enough that a gateway/model that
# later ships proper support gets re-probed instead of being pinned to the
# degraded path forever.
_LEARNED_FACT_TTL_SECONDS = 7 * 24 * 60 * 60

# The minimum non-disabled reasoning tier, used to retry a request that a
# provider rejected for setting `reasoning_effort`/`reasoning` to a
# disabled value ("none"/`False`) when that provider's model requires
# reasoning to always be on (e.g. "Reasoning is mandatory for this endpoint
# and cannot be disabled." from an OpenRouter-proxied Gemini model). "low"
# rather than `DEFAULT_REASONING_EFFORT` ("high") because the user's actual
# choice was "off" — the smallest still-legal step away from that is a
# closer match to their intent than jumping to the platform default.
REASONING_MANDATORY_FALLBACK_EFFORT = "low"


class LLMApiMode(str, Enum):
    """A learned override for how `_reasoning_effort_kwargs` should build a
    model's reasoning kwargs, keyed off an actual 400 from the provider
    rather than a name/host guess."""

    # This model needs the Responses API for reasoning + tools to work —
    # the upfront heuristic under-detected it (e.g. a gateway alias that
    # doesn't match `_is_openai_gpt5_model`).
    RESPONSES = "responses"
    # The Responses API attempt itself was rejected by whatever backend the
    # gateway actually forwards to — drop reasoning entirely and keep tools
    # on Chat Completions instead (OpenAI's own restriction is exempt for
    # reasoning "none" + tools).
    NO_REASONING_WITH_TOOLS = "no_reasoning_with_tools"
    # The provider rejected an attempt to disable reasoning outright — this
    # model/gateway requires it to stay on. Any subsequent request for
    # "none" effort is bumped to `REASONING_MANDATORY_FALLBACK_EFFORT`
    # instead.
    REASONING_MANDATORY = "reasoning_mandatory"


class LLMApiModeStore:
    """Process-local snapshot of learned per-model API-mode facts, backed by
    `config_node_constants.AI_MODEL_API_MODES`.

    The read path (`get`) is synchronous and never touches the KV store —
    `get_generator_model()` is called synchronously (and, for AWS Bedrock,
    from a worker thread via `asyncio.to_thread`), so it can only consult an
    already-loaded snapshot. Call `load()` once at service startup and again
    whenever the `llmConfigured` event fires, before `get()` can reflect
    newly stored facts.
    """

    def __init__(self, config_service: "ConfigurationService") -> None:
        self._config_service = config_service
        # {model_key: {model_name: {"mode": str, "learnedAt": float}}}
        self._snapshot: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """(Re)populate the in-process snapshot from the KV store.

        Safe to call repeatedly — each call fully replaces the snapshot
        with whatever is currently stored, so a failed load just keeps
        serving the previous snapshot rather than wiping learned facts."""
        try:
            stored = await self._config_service.get_config(
                config_node_constants.AI_MODEL_API_MODES.value, use_cache=False,
            )
            self._snapshot = self._normalize(stored)
        except Exception:
            logger.exception(
                "LLMApiModeStore: failed to load learned API modes from "
                "%s; keeping previous snapshot",
                config_node_constants.AI_MODEL_API_MODES.value,
            )

    @staticmethod
    def _normalize(stored: Any) -> dict[str, dict[str, dict[str, Any]]]:
        """Drops anything that doesn't match the expected
        ``{model_key: {model_name: {"mode": str, "learnedAt": float}}}``
        shape.

        `get()` runs synchronously on `get_generator_model()`'s hot path —
        raising there fails model construction for every request rather
        than degrading to the heuristic, so a KV entry that's missing,
        corrupted, or from a future/incompatible schema version must be
        normalized away here (where the failure is already contained by
        `load()`'s `except`) instead of trusted at read time.
        """
        if not isinstance(stored, dict):
            return {}
        normalized: dict[str, dict[str, dict[str, Any]]] = {}
        for model_key, models in stored.items():
            if not isinstance(model_key, str) or not isinstance(models, dict):
                continue
            for model_name, entry in models.items():
                if not isinstance(model_name, str) or not isinstance(entry, dict):
                    continue
                mode = entry.get("mode")
                learned_at = entry.get("learnedAt")
                if not isinstance(mode, str) or not isinstance(learned_at, (int, float)):
                    continue
                normalized.setdefault(model_key, {})[model_name] = {
                    "mode": mode, "learnedAt": float(learned_at),
                }
        return normalized

    def get(self, model_key: str | None, model_name: str) -> str | None:
        """Synchronous snapshot read — see class docstring for why this
        can't be async. Returns `None` when nothing is learned yet for this
        (model_key, model_name) pair, or the learned fact has expired."""
        if not model_key or not model_name:
            return None
        entry = (self._snapshot.get(model_key) or {}).get(model_name)
        if not entry:
            return None
        learned_at = entry.get("learnedAt", 0)
        if time.time() - learned_at > _LEARNED_FACT_TTL_SECONDS:
            return None
        return entry.get("mode")

    async def record(self, model_key: str | None, model_name: str, mode: str) -> None:
        """Persist a newly learned fact: immediately in the in-process
        snapshot (so the very next call in this pod benefits right away),
        and best-effort in the shared KV store (so other pods pick it up on
        their next `load()`).

        No-ops when the snapshot already holds this exact fact, so a hot
        agent loop hitting the same conflict repeatedly doesn't storm the
        KV store with redundant writes.
        """
        if not model_key or not model_name:
            return
        async with self._lock:
            existing = (self._snapshot.get(model_key) or {}).get(model_name)
            if existing and existing.get("mode") == mode:
                return
            self._snapshot.setdefault(model_key, {})[model_name] = {
                "mode": mode,
                "learnedAt": time.time(),
            }
            try:
                await self._config_service.set_config(
                    config_node_constants.AI_MODEL_API_MODES.value, self._snapshot,
                )
            except Exception:
                logger.exception(
                    "LLMApiModeStore: failed to persist learned API mode "
                    "(model_key=%s model_name=%s mode=%s) — kept in-process "
                    "only for this pod",
                    model_key, model_name, mode,
                )


_default_store: LLMApiModeStore | None = None


def get_llm_api_mode_store(
    config_service: "ConfigurationService | None" = None,
) -> LLMApiModeStore | None:
    """Return the process-wide `LLMApiModeStore` singleton.

    Pass `config_service` once (typically at service startup) to create it;
    every other caller (the read path in `aimodels.py`, the write path in
    `LangChainTransport`) calls this with no arguments and gets `None` back
    until that first call has happened — both call sites already treat
    `None` as "nothing learned yet" / "can't persist, skip", so an
    uninitialized store (e.g. in unit tests that construct their own
    `LLMApiModeStore` directly instead) degrades to today's heuristic-only
    behavior rather than raising.
    """
    global _default_store
    if _default_store is None and config_service is not None:
        _default_store = LLMApiModeStore(config_service)
    return _default_store


__all__ = [
    "REASONING_MANDATORY_FALLBACK_EFFORT",
    "LLMApiMode",
    "LLMApiModeStore",
    "get_llm_api_mode_store",
]
