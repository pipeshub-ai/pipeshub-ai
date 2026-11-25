"""WP82-FU-3: the delivery and answer reserves are what an answer-only turn takes, measured.

Both answer-only turns (a cut engine turn's wrap-up, a loop's answer when its time runs out) answer at most
``WRAP_UP_OUTPUT_TOKENS``. The model's part is that many tokens at the slowest rate measured on a long research run (35-45
output tokens a second); the harness's part was measured on the durable e2e lane (six parent and engine-child runs,
scripted model and engine): an engine's exit to its run completed 2.25-3.74 s, a child's completion to its parent's
answer committed 0.59-1.16 s. The old fixed 180 s and 120 s were below a slow model's capped answer.
"""

from __future__ import annotations

from harness_core.budget import run_budget as rb
from harness_core.budget.answer_size import WRAP_UP_OUTPUT_TOKENS
from harness_core.budget.wrap_up import WrapUpSize

# Measured (STATUS, FU-QUICK evidence 10).
LANE_DELIVERY_MS = (3735, 3386, 2253, 2882, 2307, 3047)
LANE_ANSWER_MS = (1160, 611, 592, 614, 777, 758)
RUN5_WRAP_UP = (8_000, 162.0)  # output tokens, seconds
RUN5_STEP_RATES = (35.0, 45.0)


def test_wp82fu3_1_the_harness_parts_cover_every_lane_measurement() -> None:
    assert max(LANE_DELIVERY_MS) / 1000 <= rb.DELIVERY_HARNESS_S
    assert max(LANE_ANSWER_MS) / 1000 <= rb.ANSWER_HARNESS_S


def test_wp82fu3_2_the_model_part_is_the_capped_answer_at_the_slowest_measured_rate() -> None:
    assert rb.SLOWEST_OUTPUT_TOKENS_PER_S == min(RUN5_STEP_RATES)
    assert WrapUpSize.for_window(None).output_tokens == WRAP_UP_OUTPUT_TOKENS
    assert rb.answer_turn_s(0.0) >= RUN5_WRAP_UP[1]


def test_wp82fu3_3_the_reserves_are_derived_and_cover_run5s_wrap_up_with_the_harness() -> None:
    assert rb.DELIVERY_RESERVE_S == rb.answer_turn_s(rb.DELIVERY_HARNESS_S) == 233
    assert rb.ANSWER_RESERVE_S == rb.answer_turn_s(rb.ANSWER_HARNESS_S) == 231
    assert rb.DELIVERY_RESERVE_S > RUN5_WRAP_UP[1] + max(LANE_DELIVERY_MS) / 1000
    assert rb.ANSWER_RESERVE_S > RUN5_WRAP_UP[1] + max(LANE_ANSWER_MS) / 1000


def test_wp82fu3_4_a_smaller_answer_takes_less() -> None:
    assert rb.answer_turn_s(2.0, output_tokens=1_000) == 31
