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
    """The reviewer's bursty parameters (one delete per 30 min, 5 % of seals dropped as stale). The round-2 lookback of four
    windows left 579 ids with no covering message; the per-org unsealed set leaves none on the reviewer's seeds, and on a
    fresh set (QA, seeds 8801-8840) 6, all in a window followed by no delete (the end of the run) or whose repaired seal was
    dropped again. Each of those keeps its durable first hop (D-182). The bounds sit just above what the real code shows."""
    reviewer = await _total(monkeypatch, range(9601, 9641), rate=1 / 1800, horizon=720_000, p_stale=0.05)
    assert reviewer["acked"] > 15_000 and reviewer["lost"] == 0, reviewer
    fresh = await _total(monkeypatch, range(8801, 8841), rate=1 / 1800, horizon=720_000, p_stale=0.05)
    assert fresh["acked"] > 15_000 and fresh["lost"] <= 8, fresh


_CRASH_FOR_GOOD = {"rate": 0.05, "horizon": 3600, "p_crash": 1.0, "die": True}
_TRUST_IN_LUA = ("if tok ~= ARGV[1] then return {'BUSY'} end",
                 "if tok ~= ARGV[1] then redis.call('RPUSH', KEYS[2], ARGV[4]) return {'APPENDED'} end")


async def test_claimers_that_die_for_good_never_strand_the_ids_that_met_their_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every claimer dies holding its claim and its event is gone: a deleter that meets the live claim must place its own id
    (the per-record second hop), never trust the claimant. The same world with the trust rule written back into the Lua
    script loses thousands of ids, so this is what guards the never-trust rule in the script itself."""
    from app.services.paid_cache import repurge_window

    good = await _total(monkeypatch, range(7301, 7321), **_CRASH_FOR_GOOD)
    assert good["acked"] > 0 and good["lost"] == 0, good
    patched = repurge_window._OPEN_OR_APPEND.replace(*_TRUST_IN_LUA)
    assert patched != repurge_window._OPEN_OR_APPEND
    monkeypatch.setattr(repurge_window, "_OPEN_OR_APPEND", patched)
    bad = await _total(monkeypatch, range(7301, 7321), **_CRASH_FOR_GOOD)
    assert bad["lost"] > 100, bad
