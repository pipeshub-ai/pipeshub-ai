"""R14-LOOP (round 14 gate, WP-66's tokenizer): a budget count never runs on the event loop's thread.

WP-66 counts a call's input and a cut call's output with the model's own BPE: milliseconds for a long history and
about 300 ms for the first load of an encoding. On the event loop that stalls every other request of the process
(the query service's ``/health`` among them). Every count on a money path runs on a worker thread, and a process
loads the encodings at startup off its loop.

R14-LOOP-1 the gateway's reservation and a cut call's estimate (Chat wire and Messages passthrough), and a finished
           call that reported no usage, count off the loop thread.
R14-LOOP-2 the loop's metered transport (reserve, and a commit that estimates) counts off the loop thread.
R14-LOOP-3 no async function of a money module or the durable driver calls a counter directly.
R14-LOOP-4 ``preload_in_background`` loads the encodings on a worker thread and holds its task until it ends.
"""

from __future__ import annotations

import ast
import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from harness_core.access import token_count
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.model_gateway import ChatStream
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)

ROOT = Path(__file__).resolve().parents[4]
STREAMED = "partial answer " * 20


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The thread of every count the counter makes."""
    seen: list[int] = []
    call_input, text = token_count.TokenCounter.call_input, token_count.TokenCounter.text

    def counted_input(self: Any, *args: Any) -> int:  # noqa: ANN401
        seen.append(threading.get_ident())
        return call_input(self, *args)

    def counted_text(self: Any, value: str) -> int:  # noqa: ANN401
        seen.append(threading.get_ident())
        return text(self, value)

    monkeypatch.setattr(token_count.TokenCounter, "call_input", counted_input)
    monkeypatch.setattr(token_count.TokenCounter, "text", counted_text)
    return seen


async def _cut(rig: GatewayRig, wire: Any, body: dict[str, Any], headers: dict[str, str]) -> None:  # noqa: ANN401
    outcome = await rig.gateway.serve(wire, rig.token(rig.pin()), headers, body_of(body))
    assert isinstance(outcome, ChatStream)
    while "partial" not in json.dumps(await anext(outcome.chunks)):
        pass
    await outcome.chunks.aclose()  # pyright: ignore[reportAttributeAccessIssue]


async def test_r14_loop_1_the_gateway_counts_off_the_loop_thread(threads: list[int]) -> None:
    loop_thread = threading.get_ident()
    chat = {"model": "engine-model", "messages": [{"role": "user", "content": "q " * 500}], "stream": True}
    messages = {**chat, "max_tokens": 700}
    for wire, body, headers in (
        (CHAT_WIRE, chat, {}),
        (MESSAGES_WIRE, messages, {"anthropic-version": "2023-06-01"}),
    ):
        rig = GatewayRig()
        rig.transport.events = [TextDeltaEvent(delta=STREAMED), reply_events("never")]
        await _cut(rig, wire, body, headers)
    rig = GatewayRig()
    rig.transport.events = [TextDeltaEvent(delta=STREAMED), reply_events("done", usage=TokenUsage(input_tokens=0, output_tokens=0))]
    outcome = await rig.gateway.serve(CHAT_WIRE, rig.token(rig.pin()), {}, body_of(chat))
    assert isinstance(outcome, ChatStream)
    _ = [chunk async for chunk in outcome.chunks]
    assert threads, "the counter ran"
    assert loop_thread not in threads


async def test_r14_loop_2_the_metered_transport_counts_off_the_loop_thread(threads: list[int]) -> None:
    loop_thread = threading.get_ident()
    account = RunAccountRef("org1", "run1")
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(account, Cap(None, 1_000_000))
    inner = RecordedTransport([reply_events("answer " * 50, usage=TokenUsage(input_tokens=0, output_tokens=0))])
    metered = MeteredTransport(inner, accounts=ledger, account=account, pricing_model="m", ids=SeededIds(0))
    _ = await metered.complete([UserMessage(content="q " * 500)], system="be brief")
    assert threads, "the counter ran"
    assert loop_thread not in threads


_COUNTERS = {"input_estimate", "output_estimate", "counter_for"}
_SCANNED = (
    "harness_core/gateway",
    "harness_core/budget",
    "harness_core/access",
    "app/harness/access",
    "harness_core/kernel/durable",
    # R15-WRAP: the engine run's wrap-up, which sizes its prompt by the run's counter.
    "harness_core/engines/durable",
)


def test_r14_loop_3_no_async_function_on_a_money_path_counts_on_the_loop() -> None:
    found: list[str] = []
    for folder in _SCANNED:
        for path in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, ast.AsyncFunctionDef):
                    continue
                found.extend(
                    f"{path.relative_to(ROOT).as_posix()}:{node.lineno} {func.name}"
                    for node in ast.walk(func)
                    if isinstance(node, ast.Call) and getattr(node.func, "id", None) in _COUNTERS
                )
    assert found == [], "count with asyncio.to_thread(...): " + ", ".join(found)


async def test_r14_loop_4_preload_runs_on_a_worker_thread_and_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[int] = []
    monkeypatch.setattr(token_count, "preload", lambda: ran.append(threading.get_ident()) or ("o200k_base",))
    task = token_count.preload_in_background()
    assert task in token_count._preloading  # pyright: ignore[reportPrivateUsage]
    assert await asyncio.wait_for(task, 10) == ("o200k_base",)
    assert ran and ran[0] != threading.get_ident()
    await asyncio.sleep(0)
    assert task not in token_count._preloading  # pyright: ignore[reportPrivateUsage]
