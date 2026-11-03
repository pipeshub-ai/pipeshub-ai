"""Live check that providers really cache what the transports mark.

Opt-in and paid: runs only with ``PIPESHUB_LIVE_PROMPT_CACHE=1`` and the
provider's credentials in the environment, otherwise every test skips. Each
test puts a fresh nonce in the stable block, so the first call writes a cache
entry no earlier run left behind.

- The same request twice: the second must read what the first wrote.
- Two conversations of one org: different questions and volatile blocks after
  the same tools and stable block. The second conversation's first call must
  read the shared prefix (on GPT-5.6 and later this needs the explicit
  breakpoint; without it the read is 0).

- Anthropic: ``TEST_ANTHROPIC_API_KEY`` or ``ANTHROPIC_API_KEY``; model from
  ``PIPESHUB_LIVE_ANTHROPIC_MODEL`` (default ``claude-haiku-5-5``).
- Azure OpenAI: ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_API_KEY``,
  ``AZURE_OPENAI_API_VERSION`` and ``AZURE_OPENAI_DEPLOYMENT``; the deployed
  model from ``PIPESHUB_LIVE_AZURE_MODEL`` (default: the deployment name).
- OpenRouter: ``OPENROUTER_API_KEY``; model from
  ``PIPESHUB_LIVE_OPENROUTER_MODEL`` (default ``anthropic/claude-haiku-5.5``).
"""

from __future__ import annotations

import asyncio
import os
import uuid
import zlib
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    CacheScope,
    PromptCacheSettings,
)
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.prompt_cache import cache_strategy_for

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.core.types import TokenUsage

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
# About 3k tokens: the prefix two conversations share.
_SHARED_RULES = "\n".join(
    f"Rule {i}: records of team {i} are filed under folder {i} and reviewed every quarter."
    for i in range(160)
)
TOOLS = [ToolSchema(name="lookup", description="Looks a record up by id.", input_schema={
    "type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"],
})]
SETTINGS = PromptCacheSettings()


def _env(*names: str) -> str:
    return next((os.environ[name] for name in names if os.environ.get(name)), "")


def _blocks() -> list[str]:
    return [f"Session {uuid.uuid4()}.\n{_RULES}", "Current time: fixed for this test."]


def _same_shard_conversations() -> tuple[str, str]:
    """Two conversation ids whose keys share a shard, as two conversations of
    one org on one shard do in production."""
    first = f"live-{uuid.uuid4()}"
    shard = zlib.crc32(first.encode()) % SETTINGS.routing_shards
    second = next(
        candidate for candidate in (f"live-{uuid.uuid4()}" for _ in range(10_000))
        if zlib.crc32(candidate.encode()) % SETTINGS.routing_shards == shard
    )
    return first, second


async def _twice(transport: LangChainTransport, blocks: list[str]) -> tuple[TokenUsage, TokenUsage]:
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


async def _two_conversations(make_transport: Callable[[str], LangChainTransport]) -> tuple[TokenUsage, TokenUsage]:
    """Each conversation's first call: the same tools and stable block, then
    its own volatile block and question."""
    stable = f"Org policy {uuid.uuid4()}.\n{_SHARED_RULES}"
    usages = []
    for index, conversation in enumerate(_same_shard_conversations()):
        blocks = [stable, f"Conversation {conversation}. Current time: 2026-10-20T1{index}:00:00Z."]
        question = [UserMessage(content=f"In conversation {index}, reply with the single word: ok")]
        response = await make_transport(conversation).complete(question, tools=TOOLS, system_blocks=blocks)
        usages.append(response.usage)
        # An entry is readable once the call that wrote it has finished.
        await asyncio.sleep(3)
    return usages[0], usages[1]


def _transport(llm: object, model: str, conversation: str | None = None) -> LangChainTransport:
    scope = CacheScope("live-test-org", conversation or str(uuid.uuid4()))
    return LangChainTransport(llm, model_name=model, cache_strategy=cache_strategy_for(
        llm, model_name=model, settings=SETTINGS, scope=scope,
    ))


def _anthropic() -> tuple[object, str]:
    api_key = _env("TEST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("no Anthropic key")
    from langchain_anthropic import ChatAnthropic

    model = os.getenv("PIPESHUB_LIVE_ANTHROPIC_MODEL", "claude-haiku-5-5")
    return ChatAnthropic(model=model, api_key=api_key, max_tokens=16), model


def _azure() -> tuple[object, str, str]:
    endpoint, api_key = _env("AZURE_OPENAI_ENDPOINT"), _env("AZURE_OPENAI_API_KEY")
    deployment = _env("AZURE_OPENAI_DEPLOYMENT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
    if not (endpoint and api_key and deployment):
        pytest.skip("no Azure OpenAI endpoint, key or deployment")
    from langchain_openai import AzureChatOpenAI

    model = os.getenv("PIPESHUB_LIVE_AZURE_MODEL", deployment)
    llm = AzureChatOpenAI(
        azure_endpoint=endpoint, api_key=api_key, azure_deployment=deployment,
        api_version=_env("AZURE_OPENAI_API_VERSION") or "2025-04-01-preview",
        # GPT-6 models reason by default and take tools only on the Responses API.
        use_responses_api=os.getenv("PIPESHUB_LIVE_AZURE_RESPONSES", "1") == "1",
        reasoning={"effort": os.getenv("PIPESHUB_LIVE_AZURE_EFFORT", "low")},
    )
    return llm, model, endpoint


def _openrouter() -> tuple[object, str]:
    api_key = _env("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("no OpenRouter key")
    from langchain_openai import ChatOpenAI

    from app.config.constants.ai_models import OPENROUTER_BASE_URL

    model = os.getenv("PIPESHUB_LIVE_OPENROUTER_MODEL", "anthropic/claude-haiku-5.5")
    return ChatOpenAI(model=model, api_key=api_key, base_url=OPENROUTER_BASE_URL, max_tokens=16,
                      stream_usage=True), model


async def test_anthropic_reads_back_what_it_wrote() -> None:
    llm, model = _anthropic()
    first, second = await _twice(_transport(llm, model), _blocks())

    assert first.cache_write_tokens > 0, first
    assert second.cache_read_tokens >= first.cache_write_tokens * 0.9, second
    assert second.input_tokens >= second.cache_read_tokens


async def test_anthropic_new_conversation_reads_the_shared_prefix() -> None:
    llm, model = _anthropic()
    first, second = await _two_conversations(lambda conversation: _transport(llm, model, conversation))

    assert first.cache_write_tokens >= 2_000, first
    assert second.cache_read_tokens >= 2_000, second


async def test_azure_openai_reads_back_with_key_and_retention() -> None:
    llm, model, endpoint = _azure()
    REJECTED_CACHE_PARAMS.clear()
    first, second = await _twice(_transport(llm, model), _blocks())

    assert second.cache_read_tokens > 0, (first, second)
    assert second.input_tokens >= second.cache_read_tokens
    assert not REJECTED_CACHE_PARAMS.refused(model, endpoint), "the endpoint refused a cache parameter"


async def test_azure_openai_new_conversation_reads_the_shared_prefix() -> None:
    llm, model, endpoint = _azure()
    REJECTED_CACHE_PARAMS.clear()
    first, second = await _two_conversations(lambda conversation: _transport(llm, model, conversation))

    assert second.cache_read_tokens >= 2_000, (first, second)
    assert not REJECTED_CACHE_PARAMS.refused(model, endpoint), "the endpoint refused a cache parameter"


async def test_openrouter_reads_back_the_whole_request() -> None:
    llm, model = _openrouter()
    first, second = await _twice(_transport(llm, model), _blocks())

    assert second.cache_read_tokens >= first.input_tokens * 0.9, (first, second)


async def test_openrouter_new_conversation_reads_the_shared_prefix() -> None:
    llm, model = _openrouter()
    first, second = await _two_conversations(lambda conversation: _transport(llm, model, conversation))

    assert second.cache_read_tokens >= 2_000, (first, second)
