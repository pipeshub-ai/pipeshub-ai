"""P4.2 LD-1, LD-2, LD-3 (choice), LD-4, LD-5 on the pure ladder."""

from __future__ import annotations

from harness_core.context import (
    Compaction,
    LadderPolicy,
    Measurement,
    SeqSpan,
    plan_reduction,
)

from .support import session

WINDOW = 100_000


def _plan(items: list, tokens: int, **kwargs: object):  # noqa: ANN202
    return plan_reduction(items, measurement=Measurement(seq=99, tokens=tokens), window_tokens=WINDOW, **kwargs)  # type: ignore[arg-type]


def test_ld1_at_or_below_half_nothing() -> None:
    assert _plan(session(8), 50_000) is None


def test_ld2_above_half_clears_results_older_than_the_recent_turns() -> None:
    items = session(8)  # input, 8 tool turns, answer
    reduction = _plan(items, 60_000, policy=LadderPolicy(keep_recent_turns=3))
    assert reduction is not None and reduction.kind == "clear"
    # The last three responses (turns 7, 8 and the answer) stay.
    assert reduction.span == SeqSpan(items[1].first_seq, items[6].last_seq)


def test_ld2_what_is_cleared_is_not_cleared_again() -> None:
    items = session(8)
    first = _plan(items, 60_000)
    assert first is not None
    assert _plan(items, 60_000, cleared=[first.span]) is None


def test_ld3_above_eighty_percent_compacts_from_the_start() -> None:
    items = session(8)
    reduction = _plan(items, 81_000)
    assert reduction is not None and reduction.kind == "compact"
    assert reduction.span == SeqSpan(1, items[6].last_seq)
    covered = Compaction(reduction.span, "s")
    # Still above 80% after a compaction that covers everything before the recent turns: the rung escalates to
    # keeping the newest turn alone instead of doing nothing (BUDGETEND), then has nothing left.
    escalated = _plan(items, 81_000, compactions=[covered])
    assert escalated is not None and escalated.span == SeqSpan(1, items[8].last_seq)
    assert _plan(items, 81_000, compactions=[covered, Compaction(escalated.span, "s2")]) is None
    assert _plan(items, 81_000, compactions=[covered], policy=LadderPolicy(keep_recent_share=None)) is None


def test_ld4_a_rising_measurement_clears_before_it_compacts() -> None:
    items = session(8)
    rungs = [_plan(items, int(WINDOW * level)) for level in (0.4, 0.6, 0.85)]
    assert [r.kind if r else None for r in rungs] == [None, "clear", "compact"]
    for level in (0.5, 0.79, 0.8):
        reduction = _plan(items, int(WINDOW * level))
        assert reduction is None or reduction.kind != "compact"


def test_ld5_the_ladder_reads_the_provider_s_count_not_the_text() -> None:
    # A history whose text any estimate would call full, measured by the provider at 10%.
    items = session(8, result_chars=60_000)
    assert _plan(items, 10_000) is None
    # A short history the provider counts at 90% is compacted.
    reduction = _plan(session(4), 90_000)
    assert reduction is not None and reduction.kind == "compact"


def test_ld6_one_reduction_per_measurement() -> None:
    items = session(8)
    stale = Measurement(seq=40, tokens=90_000)
    assert plan_reduction(items, measurement=stale, window_tokens=WINDOW, reduced_at_seq=40) is None
    assert plan_reduction(items, measurement=stale, window_tokens=WINDOW, reduced_at_seq=39) is not None


def test_no_measurement_no_reduction() -> None:
    assert plan_reduction(session(8), measurement=None, window_tokens=WINDOW) is None


def test_too_little_history_is_left_alone() -> None:
    assert _plan(session(1), 95_000) is None
