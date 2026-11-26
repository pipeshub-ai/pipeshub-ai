"""P4.3 FA-3: a run's own calls are charged the same whether they go through LangChain or a direct transport:
the transport behind a usage does not enter the charge (USAGE1)."""

from __future__ import annotations

import pytest

from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

ROOT = RunAccountRef("org-1", "root")
HI = [UserMessage(content="hi")]


async def _charged(family: str, usage: TokenUsage) -> int:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(ROOT, Cap(None, 10_000_000))
    inner = RecordedTransport(family=family)
    inner.events = [reply_events("ok", usage=usage)]
    metered = MeteredTransport(inner, accounts=ledger, account=ROOT, pricing_model="gpt-5.4", ids=SeededIds(0))
    await metered.complete(HI)
    return (await ledger.remaining(ROOT)).committed.usd_micros


@pytest.mark.parametrize("family", ["azure_direct", "langchain", "anthropic", "bedrock"])
async def test_fa3_cached_reads_are_charged_once_whatever_transport_reported_them(family: str) -> None:
    # 1,000 fresh + 4,000 read + 300 written (gpt-5.4 lists no write charge), 50 out: 2,500 + 1,000 + 0 + 750.
    usage = TokenUsage(input_tokens=5_300, output_tokens=50, cache_read_tokens=4_000, cache_write_tokens=300)
    assert await _charged(family, usage) == 4_250
