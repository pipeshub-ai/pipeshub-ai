"""Round 14 (WP-66 x WP-56): one token counter for reservations on every wire, and a cut call's hold is money only.

WP-66's counter (``harness_core.access.token_count``) sizes what a call reserves and what a cut call is charged.
WP-56 added the Messages and Chat passthroughs and per-org fairness on a shared upstream (the provider gate). These
pin how the two meet:

R14-MGW-1 a cut call's held bound stays on the budget while its gate slot is back at once (before any reconcile), so
          another org's call on the same upstream is served without waiting: a hold counts as money, never as a slot.
R14-MGW-2 the same conversation reserves, is estimated and is counted (``count_tokens``) with one number, whether it
          arrives on the Chat wire or the Messages passthrough.
R14-MGW-3 no budget or gateway module counts tokens another way: ``reservation_for`` is fed only by ``input_estimate``,
          and the kernel's four-characters counter is not imported where money is reserved.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.access.spend import input_estimate
from harness_core.access.token_count import counter_for
from harness_core.budget.types import ZERO, RunAccountRef
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.model_gateway import ChatStream
from harness_core.gateway.priority_gate import PriorityGate
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.testing.clock import ManualClock
from harness_core.testing.model_gateway import (
    ROUTE,
    GatewayRig,
    StoringTransport,
    body_of,
    reply_events,
)

if TYPE_CHECKING:
    import pytest

MODEL = ROUTE.config_model or ROUTE.model
STREAMED = "partial answer " * 20
PROMPT = "summarise the quarter, with the numbers: 12, 345, 6789 and 中文 too"
BETAS = {"anthropic-version": "2023-06-01", "anthropic-beta": "claude-code-20250219"}
ROOT = Path(__file__).resolve().parents[4]


def _chat(text: str = PROMPT) -> dict[str, Any]:
    return {"model": "engine-model", "messages": [{"role": "user", "content": text}], "stream": True}


def _messages(text: str = PROMPT) -> dict[str, Any]:
    return {"model": "engine-model", "max_tokens": 1024, "messages": [{"role": "user", "content": text}], "stream": True}


def _call_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    found = [r.getMessage() for r in caplog.records if "model_gateway.call " in r.getMessage()]
    return [dict(re.findall(r"(\w+)=(\S+)", line)) for line in found]


async def _cut(rig: GatewayRig, token: str, wire: Any, body: dict[str, Any], headers: dict[str, str]) -> None:  # noqa: ANN401
    outcome = await rig.gateway.serve(wire, token, headers, body_of(body))
    assert isinstance(outcome, ChatStream)
    while STREAMED.split()[0] not in json.dumps(await anext(outcome.chunks)):
        pass
    await outcome.chunks.aclose()  # pyright: ignore[reportAttributeAccessIssue]


async def test_r14_mgw_1_a_cut_calls_hold_is_money_never_a_gate_slot() -> None:
    clock = ManualClock()
    gate = PriorityGate(1, floor_every=0, monotonic=clock.monotonic)
    keys: list[str] = []
    original = gate.acquire

    async def recording(key: str, cls: Any, *, org: str | None = None) -> Any:  # noqa: ANN401
        keys.append(key)
        return await original(key, cls, org=org)

    gate.acquire = recording  # type: ignore[method-assign]
    rig = GatewayRig(gate=gate, transport=StoringTransport(credential="org-1-key"))
    rig.transport.events = [TextDeltaEvent(delta=STREAMED), reply_events("never")]
    await _cut(rig, rig.token(rig.pin("run-a", tenant="org-1")), CHAT_WIRE, _chat(), {})

    held = await rig.ledger.remaining(RunAccountRef("org-1", "run-a"))
    assert held.reserved != ZERO, "the cut call's bound is still held on the budget"
    assert held.estimated == 1
    assert gate.in_flight(keys[0]) == 0, "but its slot went back with the stream, before any reconcile"

    rig.transport.events = [TextDeltaEvent(delta="done"), reply_events("done")]
    outcome = await rig.gateway.serve(CHAT_WIRE, rig.token(rig.pin("run-b", tenant="org-2")), {}, body_of(_chat()))
    assert isinstance(outcome, ChatStream)
    _ = [chunk async for chunk in outcome.chunks]
    assert gate.waiting(keys[0]) == 0
    assert (await rig.ledger.remaining(RunAccountRef("org-2", "run-b"))).committed != ZERO
    await rig.gateway.reconciled()


async def test_r14_mgw_2_one_number_for_a_conversation_on_the_chat_wire_and_the_messages_passthrough(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    expected = counter_for(MODEL).call_input(
        CHAT_WIRE.parse(_chat()).messages, CHAT_WIRE.parse(_chat()).system, CHAT_WIRE.parse(_chat()).tools
    )
    parsed = MESSAGES_WIRE.parse(_messages(), BETAS)
    assert input_estimate(parsed.messages, parsed.system, parsed.tools, MODEL) == expected

    for wire, body, headers in ((CHAT_WIRE, _chat(), {}), (MESSAGES_WIRE, _messages(), BETAS)):
        rig = GatewayRig()
        rig.transport.events = [TextDeltaEvent(delta=STREAMED), reply_events("never")]
        binding = rig.pin()
        await _cut(rig, rig.token(binding), wire, body, headers)
        assert int(_call_lines(caplog)[-1]["inputTokens"]) == expected, wire.api
        counted = await rig.gateway.count_tokens(wire, rig.token(binding), headers, body_of({**body, "stream": False}))
        assert counted == expected, wire.api


_MONEY_MODULES = ("harness_core/gateway", "harness_core/budget", "harness_core/access", "app/harness/access")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for folder in _MONEY_MODULES:
        files.extend(sorted((ROOT / folder).rglob("*.py")))
    return files


def test_r14_mgw_3_no_money_module_counts_tokens_another_way() -> None:
    assert _python_files(), ROOT
    reserving: set[str] = set()
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "harness_core.kernel.core.tokens":
                names = {alias.name for alias in node.names}
                # token_count borrows only the kernel's image and text extraction, never its four-characters count.
                assert names <= {"count_image_tokens", "extract_text"}, (rel, names)
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "reservation_for":
                first = node.args[0] if node.args else None
                assert isinstance(first, ast.Name), (rel, ast.dump(first) if first else None)
                assigned = [
                    a.value
                    for a in ast.walk(tree)
                    if isinstance(a, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == first.id for t in a.targets)
                ]
                sources = {
                    getattr(getattr(v, "func", None), "id", None)
                    or getattr(getattr(v, "args", [None])[0] if getattr(v, "args", None) else None, "id", None)
                    for v in assigned
                    if isinstance(v, (ast.Call, ast.Await))
                }
                awaited = {
                    getattr(v.value.args[0], "id", None)
                    for v in assigned
                    if isinstance(v, ast.Await) and isinstance(v.value, ast.Call) and v.value.args
                }
                assert "input_estimate" in sources | awaited, (rel, first.id, sources | awaited)
                reserving.add(rel)
    assert {"harness_core/gateway/model_gateway.py", "harness_core/budget/metered.py"} <= reserving, reserving
