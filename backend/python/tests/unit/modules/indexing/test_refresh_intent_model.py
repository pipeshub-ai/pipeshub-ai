"""The reference model of the Force reindex request: the design that is built loses no request and re-buys nothing.

The model is pure Python (``tests/support/refresh_intent_model.py``); the property test next to
this one drives the same action alphabet over the repository's own handler, sweep and walk.
"""
from __future__ import annotations

import pytest

from tests.support import refresh_intent_model as model

SEEDS = (913377, 5550001, 777000111, 31337000, 8086001, 4040404)
WORLDS = 3000


def _tally(variant: str, seed: int, *, twins: bool) -> tuple[int, int, int]:
    dropped = spurious = derived = 0
    for s in range(seed, seed + WORLDS):
        drop, spur, notes, _ = model.run(s, variant, p_twin=twins)
        dropped += bool(drop)
        spurious += spur > 0
        derived += notes["spurious_any"] > 0
    return dropped, spurious, derived


@pytest.mark.parametrize("twins", [False, True])
@pytest.mark.parametrize("seed", SEEDS)
def test_final_design_loses_no_request_and_buys_nothing_twice(seed: int, twins: bool) -> None:
    assert _tally("final", seed, twins=twins) == (0, 0, 0)


@pytest.mark.parametrize("variant", ["base", "design", "fix2"])
def test_earlier_designs_still_lose_requests(variant: str) -> None:
    """The model is sensitive: today's behaviour and the first designs drop requests, so a 0 above means something."""
    assert _tally(variant, SEEDS[0], twins=True)[0] > 100


def test_upgrade_that_loses_its_origin_marker_buys_twice() -> None:
    """an in-place upgrade re-queued as an original, and a backstop that rewrites a cleared request, re-buy."""
    dropped, _, derived = _tally("fix3m", SEEDS[0], twins=True)
    assert dropped == 0 and derived > 100


def test_deciding_served_by_the_times_of_two_hosts_drops_requests() -> None:
    """The control for the clock-skew class: with the walk's host a few ticks off, comparing its request time with the time the indexing
    host recorded at completion ends requests that never ran. The built design decides by identity and does not."""
    dropped = _tally("timed", SEEDS[0], twins=False)[0]
    assert dropped > 50
    assert _tally("final", SEEDS[0], twins=False) == (0, 0, 0)
