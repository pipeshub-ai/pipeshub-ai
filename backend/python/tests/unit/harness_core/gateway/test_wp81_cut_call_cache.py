"""WP-81: a cut call's charged estimate credits the prompt cache an ended call's does (WP81-MGW).

A research run: the working-time limit cut Codex's 79th model call 318 s in. Every one of the
run's 78 earlier calls (and all of runs 3 and 4's) had the provider serve the whole previous prompt from its cache,
yet the cut call was charged as if none of its 192,368 estimated prompt tokens were cached: USD 0.44 of the run's
USD 2.06, against USD 0.16 with the credit an ended call gets. The bound the budget holds stays uncredited.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from harness_core.access.spend import usage_spend
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.model_gateway import PROMPT_CACHE_CREDIT_S
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.model_gateway import GatewayRig, body_of, reply_events
from tests.unit.harness_core.gateway.test_r13_cut_call_bound import _cut
from tests.unit.harness_core.gateway.test_wp62_codex_passthrough import (
    _call_lines,
    _chat_body,
    _estimate,
)

if TYPE_CHECKING:
    import pytest

LONG = "x" * 40_000


async def _cut_after(caplog: pytest.LogCaptureFixture, seconds: float) -> dict[str, str]:
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    rig.transport.events = [reply_events("ok", usage=TokenUsage(input_tokens=9_000, output_tokens=5, cache_read_tokens=8_900))]
    await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_chat_body(LONG, stream=False)))
    rig.clock.advance(seconds)
    await _cut(rig, token, _chat_body(LONG + " and the next step", stream=True))
    return _call_lines(caplog)[-1]


async def test_wp81_mgw_1_a_cut_engine_step_is_charged_with_the_cache_its_history_was_served_from(
    caplog: pytest.LogCaptureFixture,
) -> None:
    line = await _cut_after(caplog, 47)
    assert (line["status"], line["estimated"]) == ("interrupted", "True")
    assert int(line["cacheReadTokens"]) == min(_estimate(LONG + " and the next step"), 9_000)


async def test_wp81_mgw_2_a_cache_older_than_the_credit_window_is_not_credited(caplog: pytest.LogCaptureFixture) -> None:
    line = await _cut_after(caplog, PROMPT_CACHE_CREDIT_S + 1)
    assert int(line["cacheReadTokens"]) == 0


def test_wp81_mgw_3_run_5s_cut_call_priced_both_ways() -> None:
    """Run 5's numbers on the price table: 192,368 prompt tokens (149,817 of them the previous, cached, prompt) and
    5,710 output tokens."""
    uncredited = usage_spend(TokenUsage(input_tokens=192_368, output_tokens=5_710), "gpt-6.1-sol", charges_usd=True)
    credited = usage_spend(
        TokenUsage(input_tokens=192_368, output_tokens=5_710, cache_read_tokens=149_817), "gpt-6.1-sol", charges_usd=True
    )
    assert uncredited.usd_micros == 441_836, "what run 5's gateway logged for the call"
    assert credited.usd_micros < 160_000
