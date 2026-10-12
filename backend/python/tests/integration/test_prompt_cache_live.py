"""Live check that providers really cache what the transports mark.

Opt-in and paid: runs only with ``PIPESHUB_LIVE_PROMPT_CACHE=1`` and the
provider's credentials in the environment, otherwise every test skips. Each
test sends the same request twice behind a fresh nonce, so the first call
writes a cache entry no earlier run left behind and the second must read it.

- Anthropic: ``TEST_ANTHROPIC_API_KEY`` or ``ANTHROPIC_API_KEY``; model from
  ``PIPESHUB_LIVE_ANTHROPIC_MODEL`` (default ``claude-haiku-5-5``).
- Azure OpenAI: ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_API_KEY``,
  ``AZURE_OPENAI_API_VERSION`` and ``AZURE_OPENAI_DEPLOYMENT``; the deployed
  model from ``PIPESHUB_LIVE_AZURE_MODEL`` (default: the deployment name).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    PromptCacheSettings,
    routing_key,
)
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.prompt_cache import cache_strategy_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("PIPESHUB_LIVE_PROMPT_CACHE") != "1", reason="live, paid: set PIPESHUB_LIVE_PROMPT_CACHE=1"),
]

# Well above every provider's minimum cacheable prefix (1,024 tokens on OpenAI,
# up to 4,096 on some Claude models).
_RULES = "\n".join(
    f"Rule {i}: when a question names department {i}, answer from the records filed under department {i} only."
    for i in range(400)
)
TOOLS = [ToolSchema(name="lookup", description="Looks a record up by id.", input_schema={
    "type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"],
})]


def _env(*names: str) -> str:
    return next((os.environ[name] for name in names if os.environ.get(name)), "")


def _blocks() -> list[str]:
    return [f"Session {uuid.uuid4()}.\n{_RULES}", "Current time: fixed for this test."]


async def _twice(transport: LangChainTransport, blocks: list[str]) -> tuple:
    messages = [UserMessage(content="Reply with the single word: ok")]
    first = await transport.complete(messages, tools=TOOLS, system_blocks=blocks)
    second = None
    # OpenAI makes a new cache entry readable within seconds, not instantly.
    for _ in range(4):
        second = await transport.complete(messages, tools=TOOLS, system_blocks=blocks)
        if second.usage.cache_read_tokens:
            break
        await asyncio.sleep(3)
    return first.usage, second.usage


def _transport(llm: object, model: str) -> LangChainTransport:
    settings = PromptCacheSettings()
    key = routing_key("live-test-org", str(uuid.uuid4()), settings.routing_shards)
    return LangChainTransport(llm, model_name=model, cache_strategy=cache_strategy_for(
        llm, model_name=model, settings=settings, routing_key=key,
    ))


async def test_anthropic_reads_back_what_it_wrote() -> None:
    api_key = _env("TEST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("no Anthropic key")
    from langchain_anthropic import ChatAnthropic

    model = os.getenv("PIPESHUB_LIVE_ANTHROPIC_MODEL", "claude-haiku-5-5")
    llm = ChatAnthropic(model=model, api_key=api_key, max_tokens=16)
    first, second = await _twice(_transport(llm, model), _blocks())

    assert first.cache_write_tokens > 0, first
    assert second.cache_read_tokens >= first.cache_write_tokens * 0.9, second
    assert second.input_tokens >= second.cache_read_tokens


async def test_azure_openai_reads_back_with_key_and_retention() -> None:
    endpoint, api_key = _env("AZURE_OPENAI_ENDPOINT"), _env("AZURE_OPENAI_API_KEY")
    deployment = _env("AZURE_OPENAI_DEPLOYMENT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
    if not (endpoint and api_key and deployment):
        pytest.skip("no Azure OpenAI endpoint, key or deployment")
    from langchain_openai import AzureChatOpenAI

    REJECTED_CACHE_PARAMS.clear()
    model = os.getenv("PIPESHUB_LIVE_AZURE_MODEL", deployment)
    llm = AzureChatOpenAI(
        azure_endpoint=endpoint, api_key=api_key, azure_deployment=deployment,
        api_version=_env("AZURE_OPENAI_API_VERSION") or "2025-04-01-preview",
    )
    transport = _transport(llm, model)
    first, second = await _twice(transport, _blocks())

    assert second.cache_read_tokens > 0, (first, second)
    assert second.input_tokens >= second.cache_read_tokens
    assert not REJECTED_CACHE_PARAMS.rejected(model, endpoint), "the endpoint refused a cache parameter"
