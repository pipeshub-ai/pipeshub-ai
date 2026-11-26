# pyright: basic
"""A LangChain chat model with a per-call output limit (P4.17).

Each LangChain integration keeps its output cap in its own field and sends it on its own wire field. Setting the
field on a copy of the configured model lets each integration put the limit where its provider reads it: Chat
Completions' ``max_completion_tokens`` and Responses' ``max_output_tokens`` (``ChatOpenAI`` renames
``max_tokens`` per API), Messages' ``max_tokens``, Converse's ``maxTokens``, Gemini's ``max_output_tokens`` and
Ollama's ``num_predict``. A limit lowers a configured cap, never raises it. An integration not listed here takes
no limit, and its calls are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.models.output_bound import thinking_within, within

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

# Class name (the model's or a base's) -> the field holding its output cap.
_LIMIT_FIELDS: dict[str, str] = {
    "BaseChatOpenAI": "max_tokens",
    "ChatAnthropic": "max_tokens",
    "ChatBedrockConverse": "max_tokens",
    "ChatGoogleGenerativeAI": "max_output_tokens",
    "ChatVertexAI": "max_output_tokens",
    "ChatOllama": "num_predict",
    "ChatMistralAI": "max_tokens",
    "ChatGroq": "max_tokens",
}


def output_limit_field(llm: object) -> str | None:
    for cls in type(llm).__mro__:
        field = _LIMIT_FIELDS.get(cls.__name__)
        if field is not None:
            return field
    return None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _thinking_update(llm: object, cap: int, limit: int) -> dict[str, Any]:
    """Anthropic-family thinking counts inside ``max_tokens`` and must stay below it: a budget the limit cannot
    hold shrinks to fit, and ``max_tokens`` keeps 1,024 above it, as the providers require."""
    thinking = getattr(llm, "thinking", None)
    if isinstance(thinking, dict) and _int(thinking.get("budget_tokens")):
        budget = thinking_within(limit, thinking["budget_tokens"]) or thinking["budget_tokens"]
        return {"thinking": {**thinking, "budget_tokens": budget}, "max_tokens": max(cap, budget + 1024)}
    fields = getattr(llm, "additional_model_request_fields", None)
    nested = fields.get("thinking") if isinstance(fields, dict) else None
    if isinstance(fields, dict) and isinstance(nested, dict) and _int(nested.get("budget_tokens")):
        budget = thinking_within(limit, nested["budget_tokens"]) or nested["budget_tokens"]
        return {
            "additional_model_request_fields": {**fields, "thinking": {**nested, "budget_tokens": budget}},
            "max_tokens": max(cap, budget + 1024),
        }
    return {}


def limited(llm: BaseChatModel, limit: int | None) -> BaseChatModel:
    """``llm`` with ``limit`` on its output cap, or ``llm`` itself when there is none to apply."""
    field = output_limit_field(llm)
    if limit is None or field is None:
        return llm
    cap = within(_int(getattr(llm, field, None)), limit) or limit
    update: dict[str, Any] = {field: cap}
    update.update(_thinking_update(llm, cap, limit))
    return llm.model_copy(update=update)


__all__ = ["limited", "output_limit_field"]
