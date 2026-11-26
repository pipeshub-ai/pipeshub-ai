"""The run's model as a LangChain chat model, for the helpers that take one (WP-49 METER-ALL).

The legacy tools and their helpers (grep generation, the entity name check, a spreadsheet's table reading, the
knowledge operations) call a LangChain ``BaseChatModel``: ``ainvoke``, ``astream``, ``bind_tools``,
``with_structured_output``. Inside a harness run that model is this one: every call goes down the run's own
transport, so it is charged to the run's ledger account (budget and cost ledger), takes its provider slot at the
run's class and is checked by the classification extension point, as the run's turns are. The provider's own chat model stays inside the transport, out of the tools' reach.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, cast, override

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages import SystemMessage as LCSystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import (
    convert_to_json_schema,
    convert_to_openai_tool,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from harness_core.kernel.core.messages import TextPart
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.kernel.core.tool_schema import ToolSchema

if TYPE_CHECKING:
    from langchain_core.callbacks import (
        AsyncCallbackManagerForLLMRun,
        CallbackManagerForLLMRun,
    )
    from langchain_core.language_models import LanguageModelInput
    from langchain_core.runnables import Runnable

    from harness_core.kernel.core.messages import AssistantMessage, Message
    from harness_core.kernel.core.responses import ModelResponse, TokenUsage
    from harness_core.kernel.core.tool_schema import ToolChoice
    from harness_core.models.transports.base import LLMTransport


class RunChatModel(BaseChatModel):
    """A chat model whose every call is one of the run's transport calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True, protected_namespaces=())

    transport: Any
    model_name: str = ""
    provider_name: str = ""
    # The run scope a helper's model was handed out by (``RunHelperModels``): called outside it (kept in a
    # process-wide cache and reached by a later run or a classic chat), it would charge and check the wrong run.
    owner: Any = None

    # ``app.utils.streaming`` asks the model itself for structured output rather than guessing from its class.
    native_structured_output: ClassVar[bool] = True

    @property
    @override
    def _llm_type(self) -> str:
        return "pipeshub-run"

    @property
    def model(self) -> str:
        return self.model_name

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # A blocking call would hold the run's event loop, and with it the provider slot it waits for.
        raise NotImplementedError("the run's model answers asynchronously: use ainvoke or astream")

    @override
    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        system, history = _split(messages)
        response: ModelResponse = await self._run_transport.complete(
            history, tools=kwargs.get("tools"), system=system, tool_choice=_choice(kwargs.get("tool_choice")),
        )
        return ChatResult(generations=[ChatGeneration(message=_ai_message(response))])

    @override
    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        raise NotImplementedError("the run's model answers asynchronously: use ainvoke or astream")

    @override
    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        system, history = _split(messages)
        events = self._run_transport.stream(
            history, tools=kwargs.get("tools"), system=system, tool_choice=_choice(kwargs.get("tool_choice")),
        )
        async for event in events:
            if isinstance(event, TextDeltaEvent) and event.delta:
                chunk = ChatGenerationChunk(message=AIMessageChunk(content=event.delta))
                if run_manager is not None:
                    await run_manager.on_llm_new_token(event.delta, chunk=chunk)
                yield chunk
            elif isinstance(event, StreamCompleteEvent):
                final = _ai_message(event.response)
                # The text already streamed; the last chunk carries what only the whole answer knows.
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                            for i, c in enumerate(final.tool_calls)
                        ],
                        usage_metadata=final.usage_metadata,
                        response_metadata=final.response_metadata,
                    )
                )

    @override
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, BaseMessage]:
        schemas = [_tool_schema(tool) for tool in tools]
        return self.bind(tools=schemas, tool_choice=tool_choice, **kwargs)

    @override
    def with_structured_output(
        self,
        schema: Mapping[str, Any] | type,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, Any]:
        """``method`` and the like are not used: the run's transport asks its provider the way it supports."""
        output_schema = _json_schema(schema)

        async def structured(value: LanguageModelInput) -> Any:  # noqa: ANN401
            system, history = _split(self._convert_input(value).to_messages())
            answer = await self._run_transport.complete_structured(history, output_schema, system=system)
            raw = AIMessage(
                content=json.dumps(answer.data),
                usage_metadata=_usage(answer.usage),
                response_metadata={"model_name": answer.model or self.model_name},
            )
            try:
                parsed = _parse(schema, answer.data)
            except (ValidationError, OutputParserException) as exc:
                if include_raw:
                    return {"raw": raw, "parsed": None, "parsing_error": exc}
                raise OutputParserException(str(exc), llm_output=raw.text) from exc
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(structured, name="run_structured_output")

    @property
    def _run_transport(self) -> LLMTransport:
        if self.owner is not None:
            from app.utils.run_model_scope import current_run_model_scope

            if current_run_model_scope() is not self.owner:
                raise RuntimeError("a run's helper model was called outside the run it was built for")
        return cast("LLMTransport", self.transport)


def _split(messages: Sequence[BaseMessage]) -> tuple[str | None, list[Message]]:
    from app.agents.agent_loop.converters import convert_message_from_langchain

    system = [str(m.content) if isinstance(m.content, str) else m.text for m in messages if isinstance(m, LCSystemMessage)]
    history = [convert_message_from_langchain(m) for m in messages if not isinstance(m, LCSystemMessage)]
    return ("\n\n".join(system) or None), history


def _choice(choice: object) -> ToolChoice | None:
    # The run's transports can say "no tool calls" or leave it to the model; a forced tool is asked for as "auto".
    if choice is None:
        return None
    return "none" if choice == "none" else "auto"


def _tool_schema(tool: Any) -> ToolSchema:  # noqa: ANN401 - a LangChain tool, a function, a model class or a dict
    if isinstance(tool, ToolSchema):
        return tool
    function = cast("dict[str, Any]", convert_to_openai_tool(tool)["function"])
    return ToolSchema(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(function.get("parameters") or {"type": "object", "properties": {}}),
    )


def _json_schema(schema: Mapping[str, Any] | type) -> dict[str, Any]:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    if isinstance(schema, Mapping) and "function" not in schema and "properties" in schema:
        return dict(schema)
    return dict(convert_to_json_schema(schema))


def _parse(schema: Mapping[str, Any] | type, data: dict[str, Any]) -> Any:  # noqa: ANN401
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate(data)
    if not data:
        raise OutputParserException("the model's answer held no structured object")
    return data


def _text(message: AssistantMessage) -> str:
    return "".join(part.text for part in message.content if isinstance(part, TextPart))


def _usage(usage: TokenUsage) -> Any:  # noqa: ANN401 - LangChain's UsageMetadata
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.input_tokens + usage.output_tokens,
        "input_token_details": {"cache_read": usage.cache_read_tokens, "cache_creation": usage.cache_write_tokens},
        "output_token_details": {"reasoning": usage.reasoning_tokens},
    }


def _ai_message(response: ModelResponse) -> AIMessage:
    message = response.message
    return AIMessage(
        content=_text(message),
        tool_calls=[
            {"name": call.name, "args": dict(call.arguments), "id": call.id, "type": "tool_call"}
            for call in message.tool_calls or ()
        ],
        usage_metadata=_usage(response.usage),
        response_metadata={"model_name": response.model, "stop_reason": str(response.stop_reason.value)},
    )


__all__ = ["RunChatModel"]
