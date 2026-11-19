"""P1: the window protocol on the real code, in a discrete-event world (the reviewer's model, ported).

Oracle: every first hop that was ACKNOWLEDGED has its record's later hops covered, by a per-record second hop or by a
page hop that a seal published. Dead-lettered hops are visible (the broker's DLQ) and are not losses. The negative
controls (the round-1 rules) must lose ids in the same worlds, which proves the oracle is live.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.repurge_window import TrustingStore, World

pytestmark = pytest.mark.timeout(400)

SEEDS = range(7301, 7341)


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_RESULT_REPURGE_CHAIN", "true")
    monkeypatch.setenv("PAID_RESULT_REPURGE_COALESCE", "true")


async def _total(monkeypatch: Any, seeds: Any = SEEDS, **kw: Any) -> dict[str, int]:  # noqa: ANN401
    out = {"deletes": 0, "acked": 0, "dead": 0, "lost": 0}
    for seed in seeds:
        got = await World(seed, **kw).run(monkeypatch)
        for k, v in got.items():
            out[k] += v
    return out


@pytest.mark.parametrize(
    "kw",
    [
        {"rate": 0.15, "horizon": 1800, "outages": 2, "outage_s": 240},                       # producer outages
        {"rate": 0.15, "horizon": 1800, "p_fail": 0.02},                                    # random publish failures
        {"rate": 0.15, "horizon": 1800, "p_crash": 0.05, "outages": 1, "outage_s": 120},          # claimers die holding the claim
        {"rate": 0.15, "horizon": 1800, "outages": 1, "outage_s": 60, "flag_off_at": 900},        # the flag flips off mid-run
        {"rate": 0.02, "horizon": 7200, "p_crash": 0.1},                                    # sparse org
    ],
    ids=["outages", "publish-failures", "claimer-crashes", "flag-flip", "sparse"],
)
async def test_no_acknowledged_first_hop_loses_its_later_hops(monkeypatch: pytest.MonkeyPatch, kw: dict[str, Any]) -> None:
    got = await _total(monkeypatch, **kw)
    assert got["acked"] > 0
    assert got["lost"] == 0, got


async def test_the_round_one_rules_do_lose_ids_in_a_crashing_world(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = {"rate": 0.05, "horizon": 3600, "p_crash": 0.05, "outages": 2, "outage_s": 240}
    good = await _total(monkeypatch, **kw)
    bad = await _total(monkeypatch, store_factory=TrustingStore, **kw)
    assert good["lost"] == 0
    assert bad["lost"] > 0, bad                                  # trusting a live claim strands the window


async def test_dropped_stale_seals_are_repaired_by_the_orgs_next_window_after_a_long_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reviewer's bursty parameters (one delete per 30 min, 5 % of seals dropped as stale, seeds 9601-9640): the
    round-2 lookback of four windows left 579 ids with no covering message; the per-org unsealed set leaves the ones
    whose window is followed by no delete within the ids' life (31 in the reviewer's model). Each of those still
    has its durable first hop (D-182), the fence and the org purge."""
    got = await _total(monkeypatch, range(9601, 9641), rate=1 / 1800, horizon=720_000, p_stale=0.05)
    assert got["acked"] > 15_000
    assert got["lost"] <= 40, got
    assert got["lost"] < 579
