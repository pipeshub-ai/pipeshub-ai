"""What each strategy puts on the wire, through the real LangChain clients."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent_loop_lib.transport.prompt_cache import OpenAICachePlan
from app.agents.agent_loop.prompt_cache import (
    AnthropicCacheStrategy,
    BedrockCacheStrategy,
    CacheStrategy,
    NoCacheStrategy,
    OpenAICacheStrategy,
)

STABLE = "You are PipesHub. " * 50
VOLATILE = "Current time: 2026-10-11T00:00:00Z"
TOOLS = [
    {"type": "function", "function": {
        "name": f"tool_{i}", "description": f"Tool {i}.",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    }}
    for i in range(3)
]
FIVE_MIN = {"type": "ephemeral"}
ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}


def _history() -> list[Any]:
    return [
        HumanMessage(content="Who owns HarborBank?"),
        AIMessage(content="", tool_calls=[{"id": "t1", "name": "tool_0", "args": {"q": "HarborBank"}}]),
        ToolMessage(content="HarborBank is owned by ...", tool_call_id="t1"),
    ]


async def _send(llm: Any, strategy: CacheStrategy, *, stable: str = STABLE, volatile: str = VOLATILE,  # noqa: ANN401
                tools: list[dict] | None = None, history: list[Any] | None = None) -> None:
    messages, kwargs = strategy.finalize([*strategy.system(stable, volatile), *(history or _history())])
    bound = llm.bind_tools(strategy.tools(TOOLS if tools is None else tools)) if (tools is None or tools) else llm
    await bound.ainvoke(messages, **kwargs)


def _markers(body: Any) -> list[Any]:  # noqa: ANN401
    """Every cache_control value anywhere in a request body."""
    found: list[Any] = []

    def walk(node: Any) -> None:  # noqa: ANN401
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "cache_control":
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return found


def _strip(body: Any) -> Any:  # noqa: ANN401
    if isinstance(body, dict):
        return {k: _strip(v) for k, v in body.items() if k not in {"cache_control"}}
    if isinstance(body, list):
        return [_strip(item) for item in body if item != {"cachePoint": {"type": "default"}}]
    return body


def _anthropic(endpoint: Any) -> Any:  # noqa: ANN401
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="claude-opus-5-5", api_key="test", base_url=endpoint.url, max_retries=0)


class TestAnthropic:
    async def test_top_level_breakpoint_tools_and_stable_system(self, endpoint) -> None:
        await _send(_anthropic(endpoint), AnthropicCacheStrategy(FIVE_MIN, top_level=True))
        body = endpoint.last
        assert body["cache_control"] == FIVE_MIN
        assert [t.get("cache_control") for t in body["tools"]] == [None, None, FIVE_MIN]
        assert body["system"] == [
            {"type": "text", "text": STABLE, "cache_control": FIVE_MIN},
            {"type": "text", "text": f"\n\n{VOLATILE}"},
        ]
        assert len(_markers(body)) == 3

    async def test_tools_keep_their_definition(self, endpoint) -> None:
        await _send(_anthropic(endpoint), NoCacheStrategy())
        plain = endpoint.last["tools"]
        await _send(_anthropic(endpoint), AnthropicCacheStrategy(FIVE_MIN, top_level=True))
        assert _strip(endpoint.last["tools"]) == plain
        assert plain[0] == {
            "name": "tool_0", "description": "Tool 0.",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        }

    async def test_without_top_level_marks_the_last_message(self, endpoint) -> None:
        await _send(_anthropic(endpoint), AnthropicCacheStrategy(FIVE_MIN, top_level=False))
        body = endpoint.last
        assert "cache_control" not in body
        last_block = body["messages"][-1]["content"][-1]
        assert last_block["type"] == "tool_result"
        assert last_block["cache_control"] == FIVE_MIN
        assert len(_markers(body)) == 3

    async def test_one_ttl_everywhere(self, endpoint) -> None:
        await _send(_anthropic(endpoint), AnthropicCacheStrategy(ONE_HOUR, top_level=True))
        assert _markers(endpoint.last) == [ONE_HOUR] * 3

    async def test_empty_blocks_are_skipped(self, endpoint) -> None:
        await _send(_anthropic(endpoint), AnthropicCacheStrategy(FIVE_MIN, top_level=True), volatile="")
        assert endpoint.last["system"] == [{"type": "text", "text": STABLE, "cache_control": FIVE_MIN}]
        await _send(_anthropic(endpoint), AnthropicCacheStrategy(FIVE_MIN, top_level=True), stable="", volatile="")
        assert "system" not in endpoint.last

    async def test_no_tools_no_tool_marker(self, endpoint) -> None:
        await _send(_anthropic(endpoint), AnthropicCacheStrategy(FIVE_MIN, top_level=True), tools=[])
        assert "tools" not in endpoint.last
        assert len(_markers(endpoint.last)) == 2

    async def test_marking_never_changes_the_content(self, endpoint) -> None:
        await _send(_anthropic(endpoint), NoCacheStrategy())
        plain = endpoint.last
        for top_level in (True, False):
            await _send(_anthropic(endpoint), AnthropicCacheStrategy(FIVE_MIN, top_level=top_level))
            cached = endpoint.last
            assert _normal(cached["messages"]) == _normal(plain["messages"])
            # Adjacent blocks are read with nothing between them: joined, they are the plain prompt exactly.
            assert "".join(b["text"] for b in cached["system"]) == plain["system"]

    async def test_off_matches_today(self, endpoint) -> None:
        await _send(_anthropic(endpoint), NoCacheStrategy())
        body = endpoint.last
        assert body["system"] == f"{STABLE}\n\n{VOLATILE}"
        assert _markers(body) == []


def _normal(node: Any) -> Any:  # noqa: ANN401
    """Markers removed and string content written as the one text block it is
    shorthand for in Anthropic's API."""
    if isinstance(node, dict):
        out = {k: _normal(v) for k, v in node.items() if k != "cache_control"}
        if isinstance(out.get("content"), str):
            out["content"] = [{"type": "text", "text": out["content"]}]
        return out
    if isinstance(node, list):
        return [_normal(item) for item in node]
    return node


class TestOpenAI:
    @pytest.mark.parametrize("responses", [False, True])
    async def test_kwargs_reach_the_body(self, endpoint, responses: bool) -> None:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-5.4", api_key="test", base_url=endpoint.url, max_retries=0,
                         use_responses_api=responses)
        kwargs = {"prompt_cache_key": "pipeshub:abc:3", "prompt_cache_retention": "24h"}
        await _send(llm, OpenAICacheStrategy(OpenAICachePlan(kwargs)))
        assert endpoint.paths[-1].endswith("/responses" if responses else "/chat/completions")
        assert {k: endpoint.last.get(k) for k in kwargs} == kwargs

    async def test_system_and_tools_unchanged(self, endpoint) -> None:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-5.4", api_key="test", base_url=endpoint.url, max_retries=0)
        await _send(llm, NoCacheStrategy())
        plain = endpoint.last
        await _send(llm, OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k"})))
        cached = endpoint.last
        assert cached.pop("prompt_cache_key") == "k"
        assert cached == plain

    async def test_azure_deployment(self, endpoint) -> None:
        from langchain_openai import AzureChatOpenAI

        llm = AzureChatOpenAI(azure_endpoint=endpoint.url, azure_deployment="prod-54", api_version="2025-04-01-preview",
                              api_key="test", max_retries=0)
        await _send(llm, OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k", "prompt_cache_retention": "24h"})))
        assert "/deployments/prod-54/" in endpoint.paths[-1]
        assert endpoint.last["prompt_cache_key"] == "k"
        assert endpoint.last["prompt_cache_retention"] == "24h"

    def test_sent_params_name_the_kwargs(self) -> None:
        assert OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k"})).sent_params == {"prompt_cache_key"}
        assert OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True)).sent_params == {
            "prompt_cache_key", "prompt_cache_breakpoint",
        }


BREAKPOINT = {"mode": "explicit"}


def _gpt(endpoint: Any, *, azure: bool, responses: bool) -> Any:  # noqa: ANN401
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    if azure:
        return AzureChatOpenAI(azure_endpoint=endpoint.url, azure_deployment="gpt-6.1-sol", api_key="test",
                               api_version="2025-04-01-preview", max_retries=0, use_responses_api=responses)
    return ChatOpenAI(model="gpt-6.1-sol", api_key="test", base_url=endpoint.url, max_retries=0,
                      use_responses_api=responses)


def _system_items(body: dict, *, responses: bool) -> list[dict]:
    items = body["input"] if responses else body["messages"]
    return [item for item in items if item.get("role") in ("system", "developer")]


def _breakpoints(body: Any) -> list[Any]:  # noqa: ANN401
    found: list[Any] = []

    def walk(node: Any) -> None:  # noqa: ANN401
        if isinstance(node, dict):
            if "prompt_cache_breakpoint" in node:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return found


class TestOpenAIBreakpoint:
    """GPT-5.6 and later write the cache only at breakpoints; the stable block
    gets an explicit one so a new conversation reads tools + instructions."""

    @pytest.mark.parametrize("azure", [False, True])
    @pytest.mark.parametrize("responses", [False, True])
    async def test_the_stable_block_closes_a_marked_system_message(
        self, endpoint: Any, azure: bool, responses: bool,  # noqa: ANN401
    ) -> None:
        strategy = OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True))
        await _send(_gpt(endpoint, azure=azure, responses=responses), strategy)
        body = endpoint.last
        stable, volatile = _system_items(body, responses=responses)
        text_type = "input_text" if responses else "text"
        assert stable["content"] == [{"type": text_type, "text": STABLE, "prompt_cache_breakpoint": BREAKPOINT}]
        assert volatile["content"] == VOLATILE
        assert len(_breakpoints(body)) == 1
        assert body["prompt_cache_key"] == "k"
        assert endpoint.paths[-1].split("?")[0].endswith("/responses" if responses else "/chat/completions")

    @pytest.mark.parametrize("responses", [False, True])
    async def test_without_a_volatile_block_one_message(self, endpoint: Any, responses: bool) -> None:  # noqa: ANN401
        await _send(_gpt(endpoint, azure=True, responses=responses),
                    OpenAICacheStrategy(OpenAICachePlan({}, breakpoint=True)), volatile="")
        [stable] = _system_items(endpoint.last, responses=responses)
        assert stable["content"][0]["prompt_cache_breakpoint"] == BREAKPOINT

    @pytest.mark.parametrize("responses", [False, True])
    async def test_earlier_models_get_the_prompt_joined_as_before(self, endpoint: Any, responses: bool) -> None:  # noqa: ANN401
        await _send(_gpt(endpoint, azure=True, responses=responses), OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k"})))
        [system] = _system_items(endpoint.last, responses=responses)
        assert system["content"] == f"{STABLE}\n\n{VOLATILE}"
        assert _breakpoints(endpoint.last) == []

    @pytest.mark.parametrize("responses", [False, True])
    @pytest.mark.parametrize(
        ("prompt", "marked"),
        [({"system": STABLE}, 1), ({"system_blocks": ["", VOLATILE]}, 0)],
        ids=["one-string", "no-stable-block"],
    )
    async def test_the_direct_transport_marks_what_langchain_marks(
        self, endpoint: Any, responses: bool, prompt: dict[str, Any], marked: int,  # noqa: ANN401
    ) -> None:
        """A prompt passed as one string (a sub-agent's, a builtin role's) is
        the stable block on both paths."""
        from app.agent_loop_lib.core.messages import UserMessage
        from app.agent_loop_lib.transport.openai import OpenAITransport, RequestDefaults
        from app.agents.agent_loop.langchain_transport import LangChainTransport

        plan = OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True)
        langchain = LangChainTransport(_gpt(endpoint, azure=False, responses=responses), model_name="gpt-6.1-sol",
                                       cache_strategy=OpenAICacheStrategy(plan))
        direct = OpenAITransport(api_key="test", model="gpt-6.1-sol", base_url=endpoint.url, max_retries=0,
                                 defaults=RequestDefaults(model="gpt-6.1-sol", use_responses_api=responses),
                                 cache_plan=plan)
        for transport in (langchain, direct):
            await transport.complete([UserMessage(content="hi")], **prompt)
        via_langchain, via_direct = (_system_items(body, responses=responses) for body in endpoint.bodies)
        assert via_direct == via_langchain
        assert len(_breakpoints(via_direct)) == marked

    def test_dropping_a_refused_parameter_keeps_the_rest(self) -> None:
        strategy = OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k", "prompt_cache_retention": "24h"}, breakpoint=True))
        narrowed = strategy.without(frozenset({"prompt_cache_breakpoint"}))
        assert isinstance(narrowed, OpenAICacheStrategy)
        assert narrowed.sent_params == {"prompt_cache_key", "prompt_cache_retention"}
        assert len(narrowed.system(STABLE, VOLATILE)) == 1
        assert narrowed.without(frozenset({"prompt_cache_key", "prompt_cache_retention"})).name == "none"
        assert strategy.without(frozenset({"*"})).name == "none"
        assert strategy.without(frozenset({"cache_control"})) is strategy


class TestOpenRouterClaude:
    """Claude through OpenRouter: OpenRouter turns each breakpoint into a
    cache_control, one after the stable block and one on the last message."""

    async def test_the_stable_block_and_the_last_tool_result_are_marked(self, endpoint: Any) -> None:  # noqa: ANN401
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="anthropic/claude-sonnet-5.5", api_key="test", base_url=endpoint.url, max_retries=0)
        plan = OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True, last_message=True)
        await _send(llm, OpenAICacheStrategy(plan))
        body = endpoint.last
        assert body["messages"][0]["content"] == [{"type": "text", "text": STABLE, "prompt_cache_breakpoint": BREAKPOINT}]
        assert body["messages"][-1]["role"] == "tool"
        assert body["messages"][-1]["content"] == [
            {"type": "text", "text": "HarborBank is owned by ...", "prompt_cache_breakpoint": BREAKPOINT},
        ]
        assert len(_breakpoints(body)) == 2

    async def test_both_paths_end_on_the_relocated_tool_images(self, endpoint: Any) -> None:  # noqa: ANN401
        """Chat Completions takes images only in a user message after the tool
        results; on both paths that message is the last one, and it carries
        the last breakpoint."""
        from langchain_openai import ChatOpenAI

        from app.agent_loop_lib.core.messages import (
            AssistantMessage,
            ImagePart,
            ImageSource,
            TextPart,
            ToolCall,
            UserMessage,
        )
        from app.agent_loop_lib.core.messages import ToolMessage as LoopToolMessage
        from app.agent_loop_lib.transport.openai import OpenAITransport
        from app.agents.agent_loop.langchain_transport import LangChainTransport

        model = "anthropic/claude-sonnet-5.5"
        plan = OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True, last_message=True)
        history = [
            UserMessage(content="Who owns HarborBank?"),
            AssistantMessage(tool_calls=[ToolCall(id="t1", name="tool_0", arguments={"q": "HarborBank"})]),
            LoopToolMessage(content=[
                TextPart(text="[r1]"), ImagePart(source=ImageSource(type="url", data="https://x/1.png")),
            ], tool_call_id="t1"),
        ]
        langchain = LangChainTransport(ChatOpenAI(model=model, api_key="test", base_url=endpoint.url, max_retries=0),
                                       model_name=model, cache_strategy=OpenAICacheStrategy(plan))
        direct = OpenAITransport(api_key="test", model=model, base_url=endpoint.url, max_retries=0, cache_plan=plan)
        for transport in (langchain, direct):
            await transport.complete(list(history), system=STABLE)
        via_langchain, via_direct = (body["messages"][-1] for body in endpoint.bodies)
        assert via_direct == via_langchain
        assert via_direct["content"][1]["image_url"]["url"] == "https://x/1.png"
        assert via_direct["content"][-1]["prompt_cache_breakpoint"] == BREAKPOINT
        assert [len(_breakpoints(body)) for body in endpoint.bodies] == [2, 2]


class TestXAIKey:
    async def test_chat_xai_sends_the_key(self, endpoint: Any) -> None:  # noqa: ANN401
        from langchain_xai import ChatXAI

        llm = ChatXAI(model="grok-4.7", api_key="test", xai_api_base=endpoint.url, max_retries=0)
        await _send(llm, OpenAICacheStrategy(OpenAICachePlan({"prompt_cache_key": "k"})))
        assert endpoint.last["prompt_cache_key"] == "k"
        assert endpoint.last["messages"][0] == {"role": "system", "content": f"{STABLE}\n\n{VOLATILE}"}


def _bedrock(model_id: str) -> tuple[Any, MagicMock]:
    from langchain_aws import ChatBedrockConverse

    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
        "metrics": {"latencyMs": 1},
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }
    return ChatBedrockConverse(model=model_id, region_name="us-east-1", client=client), client


class TestBedrock:
    CACHE_POINT = {"cachePoint": {"type": "default"}}

    async def test_claude_caches_tools_system_and_messages(self) -> None:
        llm, client = _bedrock("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        await _send(llm, BedrockCacheStrategy(cache_tools=True))
        request = client.converse.call_args.kwargs
        assert request["toolConfig"]["tools"][-1] == self.CACHE_POINT
        assert len(request["toolConfig"]["tools"]) == len(TOOLS) + 1
        assert request["system"] == [{"text": STABLE}, self.CACHE_POINT, {"text": f"\n\n{VOLATILE}"}]
        assert request["messages"][-1]["content"][-1] == self.CACHE_POINT

    async def test_nova_leaves_tools_alone(self) -> None:
        llm, client = _bedrock("amazon.nova-pro-v1:0")
        await _send(llm, BedrockCacheStrategy(cache_tools=False))
        request = client.converse.call_args.kwargs
        assert self.CACHE_POINT not in request["toolConfig"]["tools"]
        assert self.CACHE_POINT in request["system"]

    async def test_off_matches_today(self) -> None:
        llm, client = _bedrock("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        await _send(llm, NoCacheStrategy())
        request = client.converse.call_args.kwargs
        assert "cachePoint" not in json.dumps(request)
        assert request["system"] == [{"text": f"{STABLE}\n\n{VOLATILE}"}]


class TestMessageMarking:
    def test_assistant_last_is_left_alone(self) -> None:
        messages = [HumanMessage(content="hi"), AIMessage(content="draft")]
        out, kwargs = AnthropicCacheStrategy(FIVE_MIN, top_level=False).finalize(messages)
        assert out == messages and kwargs == {}

    def test_input_messages_are_not_mutated(self) -> None:
        messages = [HumanMessage(content=[{"type": "text", "text": "hi"}])]
        AnthropicCacheStrategy(FIVE_MIN, top_level=False).finalize(messages)
        BedrockCacheStrategy(cache_tools=True).finalize(messages)
        assert messages[0].content == [{"type": "text", "text": "hi"}]

    def test_string_content_becomes_a_marked_block(self) -> None:
        out, _ = AnthropicCacheStrategy(FIVE_MIN, top_level=False).finalize([HumanMessage(content="hi")])
        assert out[0].content == [{"type": "text", "text": "hi", "cache_control": FIVE_MIN}]

    def test_empty_messages(self) -> None:
        for strategy in (AnthropicCacheStrategy(FIVE_MIN, top_level=False), BedrockCacheStrategy(cache_tools=True)):
            assert strategy.finalize([]) == ([], {})

    def test_strategies_never_mutate_tools(self) -> None:
        snapshot = json.dumps(TOOLS, sort_keys=True)
        AnthropicCacheStrategy(FIVE_MIN, top_level=True).tools(TOOLS)
        BedrockCacheStrategy(cache_tools=True).tools(TOOLS)
        assert json.dumps(TOOLS, sort_keys=True) == snapshot
