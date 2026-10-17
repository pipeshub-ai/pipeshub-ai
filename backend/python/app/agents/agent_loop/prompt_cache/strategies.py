"""How each provider family is asked to cache a request.

A strategy shapes the three parts of a request a provider caches in order, the
tool definitions, the system prompt and the messages, through three hooks with
no-op defaults, so a provider overrides only what it needs:

- ``system(stable, volatile)``: the system message. The stable block (the same
  for every turn and, where possible, every question of an agent) ends in a
  breakpoint; the volatile block (time, the question's context) follows it
  unmarked, so it never invalidates what precedes it.
- ``tools(tools)``: the definitions to bind, a breakpoint after the last one.
- ``finalize(messages)``: the messages and the per-call kwargs.

``sent_params`` names the request parameters the strategy adds, so a provider
error naming one of them is recognised as a rejected cache parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, SystemMessage

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

ANTHROPIC_PARAM = "cache_control"
BEDROCK_PARAM = "cachepoint"
_BEDROCK_CACHE_POINT = {"cachePoint": {"type": "default"}}


class CacheStrategy:
    """No caching: the system prompt joined as before, nothing added."""

    name = "none"
    sent_params: frozenset[str] = frozenset()

    def system(self, stable: str, volatile: str) -> SystemMessage | None:
        text = "\n\n".join(block for block in (stable, volatile) if block)
        return SystemMessage(content=text) if text else None

    def tools(self, tools: list[dict[str, Any]]) -> list[Any]:
        return list(tools)

    def finalize(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage], dict[str, Any]]:
        return messages, {}


class NoCacheStrategy(CacheStrategy):
    """Providers that cache implicitly (Gemini, ...) or not at all; also the
    strategy a transport falls back to once a provider rejects cache parameters."""


class AnthropicCacheStrategy(CacheStrategy):
    """Three breakpoints of the four a request may carry: after the tools, after
    the stable system block, and at the request's last block, through the
    top-level ``cache_control`` where the host accepts it (a marker on the last
    message elsewhere), so each turn reads all earlier turns from cache."""

    name = "anthropic"
    sent_params = frozenset({ANTHROPIC_PARAM})

    def __init__(self, marker: dict[str, str], *, top_level: bool) -> None:
        self._breakpoint = marker
        self._top_level = top_level

    def system(self, stable: str, volatile: str) -> SystemMessage | None:
        blocks: list[dict[str, Any]] = []
        if stable:
            blocks.append({"type": "text", "text": stable, "cache_control": dict(self._breakpoint)})
        if volatile:
            blocks.append({"type": "text", "text": _after(blocks, volatile)})
        return SystemMessage(content=blocks) if blocks else None

    def tools(self, tools: list[dict[str, Any]]) -> list[Any]:
        # Anthropic's own tool shape: an OpenAI-format dict would be rebuilt by
        # langchain-anthropic and lose the marker. Every tool takes this path,
        # so the definitions serialize the same way whether or not one is marked.
        native = [_anthropic_tool(tool) for tool in tools]
        if native:
            native[-1] = {**native[-1], "cache_control": dict(self._breakpoint)}
        return native

    def finalize(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage], dict[str, Any]]:
        if self._top_level:
            return messages, {ANTHROPIC_PARAM: dict(self._breakpoint)}
        return _mark_last_message(messages, {ANTHROPIC_PARAM: dict(self._breakpoint)}), {}


class BedrockCacheStrategy(CacheStrategy):
    """Converse cache points after the stable system block and on the last
    message, and after the tools for models that cache tool definitions."""

    name = "bedrock"
    sent_params = frozenset({BEDROCK_PARAM})

    def __init__(self, *, cache_tools: bool) -> None:
        self._cache_tools = cache_tools

    def system(self, stable: str, volatile: str) -> SystemMessage | None:
        blocks: list[dict[str, Any]] = []
        if stable:
            blocks.extend(({"type": "text", "text": stable}, dict(_BEDROCK_CACHE_POINT)))
        if volatile:
            blocks.append({"type": "text", "text": _after(blocks, volatile)})
        return SystemMessage(content=blocks) if blocks else None

    def tools(self, tools: list[dict[str, Any]]) -> list[Any]:
        if not self._cache_tools or not tools:
            return list(tools)
        return [*tools, dict(_BEDROCK_CACHE_POINT)]

    def finalize(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage], dict[str, Any]]:
        return _append_to_last_message(messages, dict(_BEDROCK_CACHE_POINT)), {}


class OpenAICacheStrategy(CacheStrategy):
    """OpenAI caches automatically; the request only names its cache
    (``prompt_cache_key``) and, where supported, how long to keep it."""

    name = "openai"

    def __init__(self, call_kwargs: dict[str, str]) -> None:
        self._call_kwargs = dict(call_kwargs)
        self.sent_params = frozenset(self._call_kwargs)

    def finalize(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage], dict[str, Any]]:
        return messages, dict(self._call_kwargs)


def without_cache_markers(messages: list[BaseMessage]) -> list[BaseMessage]:
    """``messages`` with every cache marker a strategy put on them removed: the
    ``cache_control`` on a block and the Converse ``cachePoint`` blocks. What a
    request sends once its provider has refused a marker."""
    stripped: list[BaseMessage] = []
    for message in messages:
        content = message.content
        if isinstance(content, str) or not any(_is_marked(block) for block in content):
            stripped.append(message)
            continue
        blocks = [
            {key: value for key, value in block.items() if key != ANTHROPIC_PARAM} if isinstance(block, dict) else block
            for block in content
            if not (isinstance(block, dict) and "cachePoint" in block)
        ]
        stripped.append(message.model_copy(update={"content": blocks}))
    return stripped


def _is_marked(block: object) -> bool:
    return isinstance(block, dict) and (ANTHROPIC_PARAM in block or "cachePoint" in block)


def _after(blocks: list[dict[str, Any]], text: str) -> str:
    """``text`` as a block that follows others: adjacent system blocks reach the
    model with nothing between them, and the separator goes on this side so the
    cached stable block is the same bytes on every request."""
    return f"\n\n{text}" if blocks else text


def _anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function") if tool.get("type") == "function" else None
    if function is None:
        return dict(tool)
    return {
        "name": function["name"],
        "description": function.get("description") or "",
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }


def _mark_last_message(messages: list[BaseMessage], marker: dict[str, Any]) -> list[BaseMessage]:
    """The last message with ``marker`` on its last content block; an assistant
    turn is left alone (its blocks may be thinking, which cannot be marked).
    String content becomes the one text block Anthropic reads it as, so the
    marked request means the same as the unmarked one."""
    if not messages or isinstance(messages[-1], AIMessage):
        return messages
    last = messages[-1]
    content = last.content
    if isinstance(content, str):
        blocks: list[Any] = [{"type": "text", "text": content}] if content else []
    else:
        blocks = [dict(block) if isinstance(block, dict) else block for block in content]
    for index in range(len(blocks) - 1, -1, -1):
        if isinstance(blocks[index], dict):
            blocks[index] = {**blocks[index], **marker}
            return [*messages[:-1], last.model_copy(update={"content": blocks})]
    return messages


def _append_to_last_message(messages: list[BaseMessage], block: dict[str, Any]) -> list[BaseMessage]:
    if not messages or isinstance(messages[-1], AIMessage):
        return messages
    last = messages[-1]
    content = last.content
    blocks: list[Any] = [{"type": "text", "text": content}] if isinstance(content, str) and content else (
        list(content) if not isinstance(content, str) else []
    )
    if not blocks:
        return messages
    return [*messages[:-1], last.model_copy(update={"content": [*blocks, block]})]
