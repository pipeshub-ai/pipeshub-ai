"""LC2FIX-6: ``/compact`` reports "before" as the provider counted the conversation's latest request (system,
tools and history), not an estimate of the history alone, and "after" as that count less what the summary
removed. Without a provider count larger than the estimate, the estimate stands."""

from __future__ import annotations

from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT
from harness_core.journal.types import CompactAsk
from tests.unit.harness_core.context.kernel_support import answered, looked_up
from tests.unit.harness_core.durable.test_slash2_c2_compact import (
    NEXT,
    _admit_next,
    _first_run,
    _next_run,
    _world,
)


async def _compacted(last_measured: int) -> dict[str, object]:
    world = _world()
    await _first_run(world, looked_up(300, "q0"), looked_up(300, "q1"), answered(tokens=last_measured))
    _admit_next(world, "and the per diem?", CompactAsk())
    await _next_run(world)
    (recorded,) = world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT)
    return recorded.data


async def test_before_is_the_providers_count_and_after_what_the_summary_left_of_it() -> None:
    estimated = await _compacted(1)
    removed = estimated["beforeTokens"] - estimated["afterTokens"]  # type: ignore[operator]
    measured = await _compacted(50_000)
    assert measured["beforeTokens"] == 50_000
    assert measured["afterTokens"] == 50_000 - removed


async def test_a_provider_count_below_the_estimate_leaves_the_estimate() -> None:
    data = await _compacted(1)
    assert isinstance(data["beforeTokens"], int) and data["beforeTokens"] > 1
    assert 0 < data["afterTokens"] < data["beforeTokens"]  # type: ignore[operator]
