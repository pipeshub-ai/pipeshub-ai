"""LLM token-usage aggregation and USD cost estimation.

Usage flow
----------
1. Call ``merge_usage_metadata(*parts)`` after each LLM stream/invoke to
   accumulate token counts from LangChain message objects.
2. Call ``resolve_pricing_id(provider, model_name, configuration)`` to get the
   canonical LiteLLM model-id for the active LLM.
3. Call ``estimate_cost_usd(pricing_id, merged_usage)`` to compute costs using
   LiteLLM's vendor-maintained price table.

For agent graphs, instantiate ``LLMUsageCallback`` and thread it through
``RunnableConfig`` so it aggregates across all sub-graph LLM calls.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider → LiteLLM prefix mapping
# Must stay in sync with LLMProvider in app/utils/aimodels.py
# ---------------------------------------------------------------------------
_PROVIDER_PREFIX: dict[str, str] = {
    "azureOpenAI": "azure",
    "bedrock": "bedrock",
    "vertexAI": "vertex_ai",
    "gemini": "gemini",
    "anthropic": "",          # no prefix — anthropic model ids are direct
    "openAI": "",             # no prefix — openai model ids are direct
    "openAICompatible": "",   # treat as openai-style; may miss from table → unknown
    "groq": "groq",
    "mistral": "mistral",
    "fireworks": "fireworks_ai",
    "cohere": "cohere",
    "together": "together_ai",
    "xai": "xai",
    "azureAI": "",            # Azure AI Foundry — try direct model id
    "ollama": "ollama",
    "minimax": "",
}


def resolve_pricing_id(
    provider: str,
    model_name: str,
    configuration: dict[str, Any] | None = None,
) -> str:
    """Return the LiteLLM-canonical model id for cost lookup.

    Resolution order:
      1. ``configuration["model"]`` (the wire-format API model name, e.g. the
         Azure deployment name or the exact API string — most accurate).
      2. ``model_name`` argument (user-facing friendly name, e.g. "gpt-4o").
      3. Empty string (will produce "unknown" pricing).

    Provider-specific prefixes are prepended so LiteLLM can find the entry:
      - azureOpenAI  → ``azure/<model>``
      - bedrock      → ``bedrock/<model>``
      - vertexAI     → ``vertex_ai/<model>``
      - groq         → ``groq/<model>``
      - etc.

    For azureOpenAI the configuration["model"] is typically a deployment name,
    not the underlying model id (e.g. "my-gpt4o-deploy" instead of "gpt-4o").
    We attempt the deployment name first; if that misses we fall back to the
    model_name arg which is usually the underlying model.
    """
    cfg = configuration or {}
    wire_model: str = (cfg.get("model") or "").strip().split(",")[0].strip()
    candidate: str = wire_model or (model_name or "").strip()

    if not candidate:
        return ""

    prefix = _PROVIDER_PREFIX.get(provider, "")
    if prefix:
        return f"{prefix}/{candidate}"
    return candidate


def merge_usage_metadata(*parts: Any) -> dict[str, Any]:
    """Merge LangChain ``usage_metadata`` dicts from a list of message parts.

    Accepts any of:
    - ``AIMessage`` / ``AIMessageChunk`` objects (reads ``.usage_metadata``)
    - raw dicts
    - ``None`` values (skipped)

    Sums ``input_tokens``, ``output_tokens``, ``total_tokens``.
    Merges ``input_token_details`` and ``output_token_details`` sub-dicts by
    summing numeric leaf values so cache_read, reasoning, etc. survive.
    """
    acc: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_token_details": {},
        "output_token_details": {},
    }
    found_any = False

    for part in parts:
        if part is None:
            continue

        usage: dict[str, Any] | None = None
        if isinstance(part, dict):
            usage = part
        elif hasattr(part, "usage_metadata") and part.usage_metadata:
            usage = part.usage_metadata
        else:
            # Some chunk aggregations expose response_metadata
            rm = getattr(part, "response_metadata", None) or {}
            usage_from_rm = rm.get("usage") or rm.get("token_usage") or rm.get("usage_metadata")
            if isinstance(usage_from_rm, dict):
                usage = usage_from_rm

        if not usage:
            continue

        found_any = True
        acc["input_tokens"] += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        acc["output_tokens"] += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        acc["total_tokens"] += int(usage.get("total_tokens") or 0)

        for detail_key in ("input_token_details", "output_token_details"):
            sub = usage.get(detail_key)
            if isinstance(sub, dict):
                target = acc[detail_key]
                for k, v in sub.items():
                    if isinstance(v, (int, float)):
                        target[k] = target.get(k, 0) + v

    if not found_any:
        return {}

    # Recompute total_tokens if providers omitted it
    if acc["total_tokens"] == 0 and (acc["input_tokens"] or acc["output_tokens"]):
        acc["total_tokens"] = acc["input_tokens"] + acc["output_tokens"]

    # Drop empty detail dicts
    if not acc["input_token_details"]:
        del acc["input_token_details"]
    if not acc["output_token_details"]:
        del acc["output_token_details"]

    return acc


def estimate_cost_usd(
    pricing_id: str,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """Compute USD cost for the given token counts using LiteLLM's price table.

    Returns a dict suitable for embedding in ``completion_data["metadata"]["llmUsage"]``:

        {
          "inputTokens": int,
          "outputTokens": int,
          "totalTokens": int,
          "inputCostUsd": float | None,
          "outputCostUsd": float | None,
          "totalCostUsd": float | None,
          "pricingSource": "litellm" | "unknown",
          "pricingModelId": str,
          "details": {
            "input_token_details": {...},   # cache_read, audio, etc.
            "output_token_details": {...},  # reasoning, etc.
          }
        }

    cost fields are ``None`` (not 0) when the model is not in LiteLLM's table.
    """
    input_tokens: int = int(usage.get("input_tokens") or 0)
    output_tokens: int = int(usage.get("output_tokens") or 0)
    total_tokens: int = int(usage.get("total_tokens") or (input_tokens + output_tokens))

    result: dict[str, Any] = {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "inputCostUsd": None,
        "outputCostUsd": None,
        "totalCostUsd": None,
        "pricingSource": "unknown",
        "pricingModelId": pricing_id or "",
    }

    # Carry forward raw detail breakdowns (cache_read, reasoning, etc.)
    details: dict[str, Any] = {}
    if usage.get("input_token_details"):
        details["input_token_details"] = usage["input_token_details"]
    if usage.get("output_token_details"):
        details["output_token_details"] = usage["output_token_details"]
    if details:
        result["details"] = details

    if not pricing_id or not input_tokens and not output_tokens:
        return result

    try:
        import litellm  # noqa: PLC0415 — lazy import; package may not be installed in all envs

        model_cost: dict = getattr(litellm, "model_cost", {}) or {}

        # Try exact id first, then lowercase
        entry = model_cost.get(pricing_id) or model_cost.get(pricing_id.lower())

        if not entry and "/" in pricing_id:
            # Some litellm entries omit the provider prefix for well-known models
            bare = pricing_id.split("/", 1)[1]
            entry = model_cost.get(bare) or model_cost.get(bare.lower())

        if entry:
            input_cost_per_token: float = entry.get("input_cost_per_token") or 0.0
            output_cost_per_token: float = entry.get("output_cost_per_token") or 0.0
            input_cost = round(input_cost_per_token * input_tokens, 6)
            output_cost = round(output_cost_per_token * output_tokens, 6)
            result.update({
                "inputCostUsd": input_cost,
                "outputCostUsd": output_cost,
                "totalCostUsd": round(input_cost + output_cost, 6),
                "pricingSource": "litellm",
            })
        else:
            logger.debug("llm_cost: model '%s' not found in litellm.model_cost", pricing_id)

    except ImportError:
        logger.warning("llm_cost: litellm not installed; cost estimation disabled")
    except Exception as exc:
        logger.warning("llm_cost: cost estimation failed for '%s': %s", pricing_id, exc)

    return result


# ---------------------------------------------------------------------------
# Agent-graph callback
# ---------------------------------------------------------------------------

class LLMUsageCallback(AsyncCallbackHandler):
    """Async LangChain callback that aggregates LLM token usage across a graph run.

    Thread this into ``RunnableConfig["callbacks"]`` before calling
    ``graph.astream(...)`` or ``graph.ainvoke(...)``.

    Usage::

        usage_cb = LLMUsageCallback()
        config = {"recursion_limit": 50, "callbacks": [usage_cb]}
        async for chunk in graph.astream(state, config=config, stream_mode="custom"):
            ...

        merged = usage_cb.merged_usage   # dict with input_tokens / output_tokens / etc.
    """

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[Any] = []
        self._lock = asyncio.Lock()

    async def on_chat_model_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Capture usage from a completed chat-model call."""
        try:
            # LangChain stores usage in LLMResult.llm_output or on generation objects
            usage: dict | None = None

            llm_output = getattr(response, "llm_output", None) or {}
            if isinstance(llm_output, dict):
                usage = (
                    llm_output.get("usage")
                    or llm_output.get("token_usage")
                    or llm_output.get("usage_metadata")
                )

            # Also try the first generation's message usage_metadata
            if not usage and response.generations:
                for gen_list in response.generations:
                    for gen in gen_list:
                        msg = getattr(gen, "message", None)
                        if msg and hasattr(msg, "usage_metadata") and msg.usage_metadata:
                            usage = msg.usage_metadata
                            break
                    if usage:
                        break

            if usage:
                async with self._lock:
                    self._parts.append(usage)
        except Exception as exc:
            logger.debug("LLMUsageCallback.on_chat_model_end error: %s", exc)

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Fallback for non-chat LLM calls."""
        await self.on_chat_model_end(response, run_id=run_id, **kwargs)

    @property
    def merged_usage(self) -> dict[str, Any]:
        """Return the merged token-usage dict accumulated so far."""
        return merge_usage_metadata(*self._parts)
