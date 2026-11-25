"""USAGE1 U1-1..U1-7: one meaning for ``TokenUsage.input_tokens``, from each provider's payload to the charge.

A request is stated once, in the provider's own terms (fresh prompt tokens, cached reads, cache writes,
output, reasoning inside the output). Each family's row builds the payload that provider returns for it and
runs it through the code that reads that payload. Every family must give the same ``TokenUsage`` (the whole
prompt in ``input_tokens``), the same charge, the same ledger debit and the same journaled usage.

The charges are frozen in ``EXPECTED_MICROS`` as the provider's own arithmetic at ``gpt-6-sol`` prices (input
2.00, read 0.20, write 2.50, output 10.00 per million). They are what the harness charged before USAGE1, when
transports kept native counts and readers normalised them.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from langchain_core.messages import AIMessage

from app.agents.agent_loop.converters import token_usage_from_ai_message
from harness_core.access.spend import usage_spend
from harness_core.access.types import PinnedRoute, RunBudget
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Amount, Cap, RunAccountRef
from harness_core.kernel.budget.pricing import get_pricing
from harness_core.kernel.budget.tracker import BudgetTracker
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.durable.context_compiler import latest_measurement
from harness_core.kernel.fold import fold_session
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.anthropic_cloud import anthropic_on_bedrock
from harness_core.models.transports.azure_openai import AzureOpenAITransport
from harness_core.models.transports.bedrock_converse import BedrockConverseTransport
from harness_core.models.transports.gemini import GeminiTransport
from harness_core.models.transports.ollama import OllamaTransport
from harness_core.models.transports.openai import OpenAITransport
from harness_core.models.transports.openai_responses import (
    responses_reasoning_tokens,
    responses_usage_fields,
)
from harness_core.testing import Reply
from harness_core.testing.clock import ManualClock
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from harness_core.testing.model_types import Usage
from tests.unit.harness_core.durable.support import SESSION, World, scripted
from tests.unit.harness_core.gateway.test_model_gateway import spent
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

MODEL = "gpt-6-sol"


@dataclass(frozen=True)
class Request:
    """One request as its provider would bill it."""

    fresh: int
    read: int = 0
    written: int = 0
    output: int = 50
    reasoning: int = 0

    @property
    def prompt(self) -> int:
        return self.fresh + self.read + self.written


REQUESTS = {
    "no_cache": Request(fresh=5_300),
    "cached_read": Request(fresh=1_300, read=4_000),
    "cache_write": Request(fresh=1_000, written=4_300),
    "read_and_write": Request(fresh=1_000, read=4_000, written=300),
    "reasoning": Request(fresh=1_000, read=4_000, reasoning=20),
}

# fresh x 2.00 + read x 0.20 + written x 2.50 + output x 10.00, in micro-USD.
EXPECTED_MICROS = {
    "no_cache": 11_100,
    "cached_read": 3_900,
    "cache_write": 13_250,
    "read_and_write": 4_050,
    "reasoning": 3_300,
}


def _anthropic_payload(r: Request) -> SimpleNamespace:
    # Messages API: input_tokens is what came after the last cache breakpoint; thinking is inside output_tokens.
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=r.fresh,
            output_tokens=r.output,
            cache_read_input_tokens=r.read,
            cache_creation_input_tokens=r.written,
        )
    )


def _anthropic(r: Request) -> TokenUsage:
    return AnthropicTransport(api_key="sk-test")._usage_from(_anthropic_payload(r))  # pyright: ignore[reportPrivateUsage]


def _anthropic_on_bedrock(r: Request) -> TokenUsage:
    transport = anthropic_on_bedrock(
        region="us-east-1", access_key_id="AKIA" + "A" * 16, secret_access_key="s" * 40, model="claude-sonnet-4-6"
    )
    return transport._usage_from(_anthropic_payload(r))  # pyright: ignore[reportPrivateUsage]


def _chat_payload(r: Request) -> SimpleNamespace:
    # Chat Completions: prompt_tokens is the whole prompt; the cached parts are details of it.
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=r.prompt,
            completion_tokens=r.output,
            prompt_tokens_details=SimpleNamespace(cached_tokens=r.read, cache_write_tokens=r.written),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=r.reasoning),
        )
    )


def _openai_chat(r: Request) -> TokenUsage:
    return OpenAITransport(api_key="sk-test")._usage_from(_chat_payload(r))  # pyright: ignore[reportPrivateUsage]


def _openai_responses(r: Request) -> TokenUsage:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=r.prompt,
            output_tokens=r.output,
            input_tokens_details=SimpleNamespace(cached_tokens=r.read, cache_write_tokens=r.written),
            output_tokens_details=SimpleNamespace(reasoning_tokens=r.reasoning),
        )
    )
    transport = OpenAITransport(api_key="sk-test")
    return transport._record_usage(  # pyright: ignore[reportPrivateUsage]
        *responses_usage_fields(response), reasoning=responses_reasoning_tokens(response)
    )


def _azure_openai(r: Request) -> TokenUsage:
    transport = AzureOpenAITransport(
        api_key="k", azure_endpoint="https://example.openai.azure.com", api_version="2024-10-01-preview", deployment=MODEL
    )
    return transport._usage_from(_chat_payload(r))  # pyright: ignore[reportPrivateUsage]


def _bedrock_converse(r: Request) -> TokenUsage:
    # Converse: inputTokens leaves cached tokens out; totalTokens is the sum of all four.
    return BedrockConverseTransport._usage(  # pyright: ignore[reportPrivateUsage]
        {
            "inputTokens": r.fresh,
            "outputTokens": r.output,
            "cacheReadInputTokens": r.read,
            "cacheWriteInputTokens": r.written,
            "totalTokens": r.prompt + r.output,
        }
    )


def _gemini(r: Request) -> TokenUsage:
    # generateContent: prompt_token_count includes cached content; thoughts are billed output, counted apart.
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=r.prompt,
            cached_content_token_count=r.read,
            candidates_token_count=r.output - r.reasoning,
            thoughts_token_count=r.reasoning,
        )
    )
    return GeminiTransport(api_key="k", model="gemini-3-flash-preview")._record_usage_from(response)  # pyright: ignore[reportPrivateUsage]


def _ollama(r: Request) -> TokenUsage:
    return OllamaTransport()._usage_from({"prompt_eval_count": r.prompt, "eval_count": r.output})  # pyright: ignore[reportPrivateUsage]


def _langchain(r: Request) -> TokenUsage:
    # usage_metadata: input_tokens sums every input token, for every provider behind LangChain.
    return token_usage_from_ai_message(
        AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": r.prompt,
                "output_tokens": r.output,
                "total_tokens": r.prompt + r.output,
                "input_token_details": {"cache_read": r.read, "cache_creation": r.written},
                "output_token_details": {"reasoning": r.reasoning},
            },
        )
    )


@dataclass(frozen=True)
class Family:
    usage: Callable[[Request], TokenUsage]
    writes: bool = True
    reads: bool = True
    # Whether the payload states reasoning apart; it is inside ``output_tokens`` either way.
    reasoning: bool = True


FAMILIES = {
    "anthropic": Family(_anthropic, reasoning=False),
    "anthropic_on_bedrock": Family(_anthropic_on_bedrock, reasoning=False),
    # Chat Completions rows have no cache write until the transport reads ``cache_write_tokens`` (main's does;
    # usage1.md, finding F1): set ``writes`` then, the expected values are already here.
    "openai_chat": Family(_openai_chat, writes=False),
    "openai_responses": Family(_openai_responses),
    "azure_openai": Family(_azure_openai, writes=False),
    "bedrock_converse": Family(_bedrock_converse, reasoning=False),
    "gemini": Family(_gemini, writes=False),
    "ollama": Family(_ollama, writes=False, reads=False, reasoning=False),
    "langchain": Family(_langchain),
}

ROWS = [
    pytest.param(family, kind, id=f"{family}-{kind}")
    for family, spec in FAMILIES.items()
    for kind, request in REQUESTS.items()
    if (spec.writes or not request.written) and (spec.reads or not request.read)
]


def _expected(family: str, kind: str) -> TokenUsage:
    request = REQUESTS[kind]
    return TokenUsage(
        input_tokens=request.prompt,
        output_tokens=request.output,
        cache_read_tokens=request.read,
        cache_write_tokens=request.written,
        reasoning_tokens=request.reasoning if FAMILIES[family].reasoning else 0,
    )


def _micros(usd: float) -> int:
    return round(usd * 1_000_000)


@pytest.mark.parametrize(("family", "kind"), ROWS)
def test_u1_1_to_4_a_provider_payload_becomes_one_usage_and_one_charge(family: str, kind: str) -> None:
    request = REQUESTS[kind]
    usage = FAMILIES[family].usage(request)

    assert usage == _expected(family, kind)
    assert usage.uncached_input_tokens == request.fresh
    assert usage.reasoning_tokens <= usage.output_tokens

    cost = get_pricing(MODEL).cost_usd(
        usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens
    )
    assert _micros(cost) == EXPECTED_MICROS[kind]
    # The token debit weighs a cached read at gpt-6-sol's read price ratio, 0.20 / 2.00 (ENGBUDGET).
    tokens = request.prompt - usage.cache_read_tokens + math.ceil(usage.cache_read_tokens / 10) + request.output
    assert usage_spend(usage, MODEL, charges_usd=True) == Amount(EXPECTED_MICROS[kind], tokens)
    assert usage_spend(usage, MODEL, charges_usd=False) == Amount(0, tokens)


@pytest.mark.parametrize(("family", "kind"), ROWS)
async def test_u1_1_to_4_the_journal_records_that_usage(family: str, kind: str) -> None:
    request = REQUESTS[kind]
    usage = FAMILIES[family].usage(request)
    world = World()
    model, _ = scripted(
        Reply(
            text="done",
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            ),
        )
    )
    # Journaled under the family's own name: the reading of a marked usage must not depend on it.
    port = ScriptedModelPort(model, provider=family.replace("_", "-"), transport="direct")
    world.admit()
    _ = await world.run(world.kernel(port))

    [response] = world.events("model.response")
    journaled: dict[str, JsonValue] = {
        "inputTokens": request.prompt,
        "outputTokens": request.output,
        "cacheReadTokens": request.read,
        "cacheWriteTokens": request.written,
    }
    if request.read or request.written:
        journaled["inputIncludesCached"] = True
    assert response.data["usage"] == journaled

    session = fold_session(world.journal.events(SESSION), stream_id=SESSION)
    measured = latest_measurement(session)
    assert measured is not None and measured.tokens == request.prompt


@pytest.mark.parametrize("kind", sorted(REQUESTS))
def test_u1_6_every_family_that_can_state_a_request_states_it_the_same(kind: str) -> None:
    request = REQUESTS[kind]
    stated = {
        name: spec.usage(request).model_copy(update={"reasoning_tokens": 0})
        for name, spec in FAMILIES.items()
        if (spec.writes or not request.written) and (spec.reads or not request.read)
    }
    assert len(stated) >= 2
    assert len({usage.model_dump_json() for usage in stated.values()}) == 1, stated


ROOT = RunAccountRef("org-1", "root")


async def _metered(usage: TokenUsage, model: str, family: str) -> int:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, Cap(None, None))
    inner = RecordedTransport([reply_events("ok", usage=usage)], family=family)
    metered = MeteredTransport(inner, accounts=ledger, account=ROOT, pricing_model=model, ids=SeededIds(0))
    await metered.complete([UserMessage(content="hi")])
    return (await ledger.remaining(ROOT)).committed.usd_micros


async def _gateway(usage: TokenUsage, model: str, family: str, wire: str) -> int:
    rig = GatewayRig()
    transport = RecordedTransport([reply_events("ok", usage=usage)], family=family)
    route = PinnedRoute(family, "gpt-x", config_key="key-1", config_model=model)
    binding = rig.pin(routes=(route,), transport=transport, budget=RunBudget(max_cost_usd=1_000.0, max_tokens=None))
    codec, body = WIRES[wire]
    _ = await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body()))
    return (await spent(rig)).usd_micros


async def _tracker(usage: TokenUsage, model: str) -> float:
    tracker = BudgetTracker(model=model)
    await tracker.record_turn(
        usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens
    )
    return (await tracker.snapshot()).cost_usd


# On both sides of the long-context threshold (272,000 prompt tokens): every reader picks the tier per call (MA1
# closed F2, the tracker pricing one tier).
_TOKENS = st.integers(min_value=0, max_value=150_000)


@settings(max_examples=40, deadline=None)
@given(
    fresh=_TOKENS,
    read=_TOKENS,
    written=_TOKENS,
    output=st.integers(min_value=1, max_value=60_000),
    model=st.sampled_from(["gpt-6-sol", "gpt-5.4", "claude-sonnet-4-6", "a-model-the-table-does-not-list"]),
    family=st.sampled_from(["openai", "azure_direct", "anthropic", "gemini", "bedrock", "langchain"]),
    wire=st.sampled_from(sorted(WIRES)),
)
@example(fresh=150_000, read=100_000, written=50_000, output=1_000, model="gpt-6-sol", family="openai", wire="chat")
def test_u1_5_the_cost_does_not_depend_on_which_reader_computed_it(
    fresh: int, read: int, written: int, output: int, model: str, family: str, wire: str
) -> None:
    usage = TokenUsage(
        input_tokens=fresh + read + written, output_tokens=output, cache_read_tokens=read, cache_write_tokens=written
    )
    pricing = get_pricing(model, prompt_tokens=usage.input_tokens)
    by_parts = (
        fresh * pricing.input_price_per_mtok
        + read * pricing.cache_read_price_per_mtok
        + written * pricing.cache_write_price_per_mtok
        + output * pricing.output_price_per_mtok
    ) / 1_000_000
    ledger_debit = usage_spend(usage, model, charges_usd=True).usd_micros

    assert pricing.cost_usd(usage.input_tokens, output, read, written) == pytest.approx(by_parts, abs=1e-12)
    assert ledger_debit - 1 <= by_parts * 1_000_000 <= ledger_debit
    assert asyncio.run(_tracker(usage, model)) == pytest.approx(by_parts, abs=1e-12)
    assert asyncio.run(_metered(usage, model, family)) == ledger_debit
    assert asyncio.run(_gateway(usage, model, family, wire)) == ledger_debit
