"""The pure parts of the pgvector search path: the tier chooser, fusion, the knobs and the membership fragments."""

from __future__ import annotations

import dataclasses

import pytest

from app.services.vector_db.pgvector import dense, fusion
from app.services.vector_db.pgvector.tuning import Tuning
from app.services.vector_db.pgvector.visibility import Scope

T = Tuning()
N = T.n_e0


def sizing(n: int, *, own: bool = False, leaf: int = 10_000_000, capped: bool = False) -> dense.Sizing:
    return dense.Sizing(n, capped, own, leaf)


@pytest.mark.parametrize(("sz", "tier"), [
    (sizing(0), dense.Tier.E0),
    (sizing(N), dense.Tier.E0),                                              # up to n_e0: exact, in any leaf at any share
    (sizing(N, leaf=N), dense.Tier.E0),
    (sizing(N + 1, own=True), dense.Tier.H),                                 # an org that owns its leaf: the vector index
    (sizing(N + 1, leaf=100_000_000), dense.Tier.H),                         # a shared leaf, share far below theta
    (sizing(N + 1, leaf=N + 1000), dense.Tier.E0X),                          # awaiting promotion: exact up to 4 x n_e0
    (sizing(4 * N, leaf=5 * N), dense.Tier.E0X),
    (sizing(4 * N + 1, leaf=5 * N), dense.Tier.H_DEEP),                      # ... and a deeper index tier above
    (sizing(4 * N + 1, own=True, capped=True), dense.Tier.H),
    (sizing(4 * N + 1, leaf=100_000_000, capped=True), dense.Tier.H),        # capped but a tiny share of a shared leaf: the index
    (sizing(4 * N + 1, leaf=500_000, capped=True), dense.Tier.H_DEEP),       # capped and 32 % of a shared leaf: awaiting promotion
    (sizing(N + 1, leaf=0), dense.Tier.H),                                   # a leaf never analysed: share unknown, not "due"
])
def test_the_tier_follows_candidates_leaf_and_share(sz: dense.Sizing, tier: dense.Tier) -> None:
    assert dense.choose(sz, 40, T).tier is tier


def test_the_rerank_depth_and_budgets() -> None:
    assert dense.choose(sizing(N + 1, own=True), 40, T).r == 800                       # max(800, 20 x leg)
    assert dense.choose(sizing(N + 1, own=True), 120, T).r == 2400
    assert dense.choose(sizing(4 * N + 1, leaf=5 * N), 40, T).r == 4000                # max(4000, 50 x leg)
    assert dense.choose(sizing(4 * N + 1, leaf=5 * N), 120, T).r == 6000
    assert [dense.choose(sizing(N + 1, own=True), leg, T).timeout_ms for leg in (40, 100, 120, 600)] == [1000, 1500, 1500, 3000]
    assert dense.choose(sizing(5), 40, T).timeout_ms == 300


def test_only_a_big_exact_scan_or_the_vector_index_takes_an_admission_slot() -> None:
    assert dense.choose(sizing(9_999), 40, T).slot is None
    assert dense.choose(sizing(10_001), 40, T).slot == "e0"
    assert dense.choose(sizing(N + 1, own=True), 40, T).slot == "h"


def test_a_space_that_failed_its_recall_check_is_served_exactly_above_n_e0() -> None:
    assert dense.choose(sizing(N + 1, own=True), 40, T, bq_ok=False).tier is dense.Tier.E0X
    assert dense.choose(sizing(5), 40, T, bq_ok=False).tier is dense.Tier.E0


def test_share_bands() -> None:
    assert dense.share_band(sizing(1, own=True), 0.05) == "own_leaf"
    assert dense.share_band(sizing(10, leaf=10_000), 0.05) == "lt_1pct"
    assert dense.share_band(sizing(300, leaf=10_000), 0.05) == "1pct_to_theta"
    assert dense.share_band(sizing(500, leaf=10_000), 0.05) == "awaiting_promotion"


def test_fusion_matches_the_one_leg_and_two_leg_scores_of_qdrant() -> None:
    assert fusion.rrf([["a", "b", "c"]], 2) == [("a", 0.5), ("b", pytest.approx(1 / 3))]
    both = dict(fusion.rrf([["a", "b"], ["b", "c"]], 10))
    assert both == {"b": pytest.approx(1 / 3 + 1 / 2), "a": 0.5, "c": pytest.approx(1 / 3)}
    assert fusion.rrf([["b"], ["a"]], 2) == [("a", 0.5), ("b", 0.5)]                    # equal scores: by id, every time


def test_every_knob_has_a_default_and_a_value_that_is_not_one_is_refused() -> None:
    assert Tuning.from_dict(None) == Tuning() and Tuning.from_dict({}).n_e0 == 40_000
    t = Tuning.from_dict({"validate_quantization": "off", "e0_slots": "12", "r_min": 900.0})
    assert t.validate_quantization is False
    assert (t.e0_slots, t.r_min) == (12, 900)                                      # good values pass, strings and floats included
    for bad in ({"n_e0": "lots"}, {"promotion_share": float("nan")}, {"validate_quantization": "maybe"}, {"h_slots": -3}, {"sampler_rate": 7},
                {"ef_cap": 5000}):                                                 # not a number or a switch, or outside the range: refused
        with pytest.raises(ValueError, match="PGVECTOR_"):
            Tuning.from_dict(bad)


def test_environment_variables_set_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGVECTOR_H_SLOTS", "7")
    monkeypatch.setenv("PGVECTOR_VALIDATE_QUANTIZATION", "false")
    t = Tuning.from_dict({})
    assert t.h_slots == 7 and t.validate_quantization is False


@pytest.mark.parametrize(("work_mem_mb", "multiplier"), [(64, 4), (4, 2), (64, 1), (1024, 1), (7, 3)])
def test_scan_memory_is_at_least_256_mb_for_any_setting(work_mem_mb: int, multiplier: int) -> None:
    mb, mult = Tuning(scan_work_mem_mb=work_mem_mb, scan_mem_multiplier=multiplier).scan_memory()
    assert mb * mult >= 256




def test_sql_of_every_tier_binds_every_parameter_it_is_sent() -> None:
    q = dense.Query("a" * 24, 1, 1, 8, "cosine", "[1]", Scope(["c"], [], [], [], None), 10)
    for sql in (dense.exact_sql(q), dense.index_sql(q), dense.bounded_sql(q, narrow=False), dense.count_sql(q)):
        assert "$4::ext.halfvec" in sql and "$9::text[]" in sql                          # all of $4..$9 typed, used or not
        assert "OPERATOR(ext.<=>)" in sql or "count(*)" in sql
    assert "<~>" in dense.index_sql(q) and "<~>" not in dense.exact_sql(q) and "+ 0" in dense.exact_sql(q)
    assert "ORDER BY p.point_sk DESC" in dense.bounded_sql(q, narrow=False)
    dot = dense.Query("a" * 24, 1, 1, 8, "dot", "[1]", Scope(), 10)
    assert "OPERATOR(ext.<#>)" in dense.exact_sql(dot)




class _Tx:
    def __init__(self, answers: list) -> None:
        self.answers, self.calls = answers, 0

    async def fetchval(self, sql: str, *args: object) -> object:
        self.calls += 1
        return self.answers.pop(0) if self.answers else None


async def test_admission_retries_until_a_slot_frees_and_gives_up_after_the_wait() -> None:
    tx = _Tx([None, None, 5])
    await dense.admit(tx, "h", 8, 1000)
    assert tx.calls == 3
    full = _Tx([])
    with pytest.raises(dense.Rejected):
        await dense.admit(full, "e0", 8, 30)
    assert full.calls >= 2                                         # it did retry inside the wait
    none = _Tx([])
    with pytest.raises(dense.Rejected):
        await dense.admit(none, "h", 8, 0)
    assert none.calls == 1


def test_sampled_recall_is_measured_against_the_exact_rows_of_the_same_candidates() -> None:
    from app.services.vector_db.pgvector.sampler import recall

    exact = list(range(100, 160))
    assert recall(frozenset(exact[:40]), exact, 40) == 1.0
    assert recall(frozenset(exact[20:60]), exact, 40) == 0.5
    assert recall(frozenset(exact[40:80]), exact, 40) == 0.0                 # planted rows of exact ranks 40-80 do not pass
    assert recall(frozenset(range(1, 41)), exact, 40) == 0.0                 # planted rows that are not neighbours at all
    assert recall(frozenset(exact[:3]), exact[:3], 40) == 1.0                # fewer candidates than the leg: the divisor is what exists
    assert recall(frozenset(), [], 40) is None                               # nothing to compare


def test_the_sampler_slows_down_when_its_own_database_time_is_too_high() -> None:
    from app.services.vector_db.pgvector.sampler import RATE_FLOOR, Sampler

    now = [1000.0]
    s = Sampler(object(), Tuning.from_dict({"sampler_rate": 1.0}), clock=lambda: now[0], cores=2)
    assert s._throttle() == 1.0
    s._busy.append((now[0], 18.0))                                          # 3x the allowance (5 % of two cores for a minute = 6 s)
    assert s._throttle() == pytest.approx(1 / 3)
    s._busy.append((now[0], 10_000.0))
    assert s._throttle() == RATE_FLOOR
    now[0] += 61
    assert s._throttle() == 1.0                                              # the minute passed


def test_the_quantisation_check_scans_less_on_a_big_leaf() -> None:
    from app.services.vector_db.pgvector.validate import samples_for

    assert samples_for(T, 5_000) == 50
    assert samples_for(T, 1_000_000) == 30
    assert samples_for(T, 100_000_000) == 5


# ---- round 2 -----------------------------------------------------------------------------------------------------------------------



def test_an_own_leaf_uses_the_index_above_n_own_when_the_filter_keeps_a_tenth_of_the_leaf() -> None:
    assert dense.count_cap(T) == 4 * N + 1
    assert dense.count_cap(T, (False, 1_000_000)) == 4 * N + 1                      # a shared leaf
    assert dense.count_cap(T, (True, 25_000)) == 10_001                             # n_own + 1 already is 40 % of this leaf
    assert dense.count_cap(T, (True, 400_000)) == 40_000                            # 10 % of the leaf
    assert dense.count_cap(T, (True, 5_000_000)) == N + 1                           # never past n_e0 + 1
    assert dense.count_cap(T, (True, 0)) == 4 * N + 1                               # leaf size unknown
    assert dense.choose(sizing(10_001, own=True, leaf=25_000, capped=True), 40, T).tier is dense.Tier.H
    assert dense.choose(sizing(30_000, own=True, leaf=1_000_000), 40, T).tier is dense.Tier.E0         # narrow filter: exact up to n_e0
    assert dense.choose(sizing(10_000, own=True, leaf=25_000), 40, T).tier is dense.Tier.E0
    assert dense.choose(sizing(N + 1, own=True, leaf=5_000_000), 40, T).tier is dense.Tier.H
    assert dense.choose(sizing(30_000, leaf=60_000), 40, T).tier is dense.Tier.E0                      # shared leaf: exact up to n_e0 at any share


def test_a_space_that_failed_its_check_is_never_scanned_without_bound() -> None:
    big = dense.choose(sizing(4 * N + 1, own=True, capped=True), 40, T, bq_ok=False)
    assert big.tier is dense.Tier.H_DEEP and big.flag == "quantization" and big.r == 2 * 4000
    mid = dense.choose(sizing(2 * N, own=True, leaf=2 * N), 40, T, bq_ok=False)
    assert mid.tier is dense.Tier.E0X and mid.flag == "quantization"
    assert dense.choose(sizing(5), 40, T, bq_ok=False).flag is None


def test_the_degraded_path_works_by_groups_when_the_filter_is_narrow() -> None:
    q = dense.Query("a" * 24, 1, 1, 8, "cosine", "[1]", Scope(["c"], [], [], []), 10)
    assert "IN (SELECT m.group_key" in dense.bounded_sql(q, narrow=True) and "group_members" in dense.bounded_sql(q, narrow=True) and "EXISTS (SELECT 1 FROM retrieval.group_members" not in dense.bounded_sql(q, narrow=True)
    assert "EXISTS (SELECT 1 FROM retrieval.group_members" in dense.bounded_sql(q, narrow=False)
    assert "= ANY (SELECT t.point_sk" in dense.exact_sql(q)          # tombstones: one hashed set, not a probe per row


def test_a_degraded_sample_does_not_count_against_the_org(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app.services.vector_db.pgvector import sampler

    async def exact(*a: object, **kw: object) -> list[tuple[int, str]]:
        return [(i, str(i)) for i in range(20)]

    monkeypatch.setattr(dense, "exact", exact)
    s = sampler.Sampler(object(), Tuning.from_dict({"sampler_rate": 1.0}))
    q = dense.Query("a" * 24, 1, 1, 8, "cosine", "[1]", Scope(), 20)
    asyncio.run(s.measure(sampler.Sample(q, frozenset(), "degraded", "own_leaf", 5)))
    assert s.org_recall("a" * 24) is None
    asyncio.run(s.measure(sampler.Sample(q, frozenset(range(20)), "h", "own_leaf", 5)))
    assert s.org_recall("a" * 24) == (1.0, 1)


def test_a_search_result_says_whether_it_is_complete() -> None:
    from app.services.vector_db.models import SearchResult

    assert SearchResult(id="a", score=1.0).degraded is None
    assert SearchResult(id="a", score=1.0, degraded="admission").degraded == "admission"
    assert SearchResult(id="a", score=1.0, degraded="admission") == SearchResult(id="a", score=1.0)       # a flag is not part of the ranking


def test_tombstones_are_tested_as_one_hashed_set_only_while_that_is_safe() -> None:
    q = dense.Query("a" * 24, 1, 1, 8, "cosine", "[1]", Scope(), 10)
    anti = dataclasses.replace(q, anti=True)
    assert "= ANY (SELECT t.point_sk" in dense.exact_sql(q) and "NOT EXISTS" not in dense.exact_sql(q)
    assert "NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones" in dense.exact_sql(anti) and "= ANY (SELECT t.point_sk" not in dense.index_sql(anti)
    for narrow in (True, False):                                  # the degraded path is bounded whatever the count
        assert "NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones" in dense.bounded_sql(q, narrow=narrow)


def test_the_index_tiers_membership_probe_is_per_row_not_a_hashed_read_of_every_admitted_row() -> None:
    from app.services.vector_db.pgvector import visibility

    s = Scope(["c"], ["g"], [], [])
    assert "OFFSET 0)" in s.probe() and "OFFSET" not in s.candidates()
    assert visibility.PER_ROW_PROBE is True


def test_the_index_tier_always_tests_tombstones_one_row_at_a_time() -> None:
    q = dense.Query("a" * 24, 1, 1, 8, "cosine", "[1]", Scope(), 10)
    for query in (q, dataclasses.replace(q, anti=True)):
        sql = dense.index_sql(query)
        assert "t.point_sk = p.point_sk OFFSET 0)" in sql and "= ANY (SELECT t.point_sk" not in sql


# ---- one statement per search ----------------------------------------------------------------------------------------------------------

def _fused_plan(tier: dense.Tier) -> dense.Plan:
    return dense.Plan(tier, 800, 1000, None, "own_leaf")






async def test_a_rolled_sample_is_queued_without_a_second_throw() -> None:
    from app.services.vector_db.pgvector.sampler import Sample, Sampler

    draws = iter([0.0, 0.9])
    s = Sampler(object(), Tuning.from_dict({"sampler_rate": 0.5}), rng=lambda: next(draws), cores=2)
    q = dense.Query("a" * 24, 1, 1, 8, "cosine", "[1]", Scope(), 10)
    sample = Sample(q, frozenset(), "h", "own_leaf", 1)
    assert s.roll() is True and s.roll() is False
    assert s.offer(sample, rolled=True) is True                                # no draw is taken for a sample that was rolled before
    assert Sampler(object(), Tuning.from_dict({"sampler_rate": 0.0}), cores=2).roll() is False


async def test_queries_of_one_filter_that_miss_together_share_one_measurement() -> None:
    import asyncio

    class _Db:
        runs = 0

        async def run(self, fn: object, **_: object) -> tuple:
            _Db.runs += 1
            await asyncio.sleep(0.01)
            return ("points_o_x", 5_000_000), 12_345, False, 0

    q = dense.Query("o" * 24, 1, 1, 4, "cosine", "[0,0,0,0]", Scope([], ["g"], [], []), 40)
    sizer = dense.Sizer(_Db(), T)
    got = await asyncio.gather(*(sizer.of(q) for _ in range(4)))
    assert _Db.runs == 1 and len({*got}) == 1                       # one count, the same Sizing for every waiter
    assert got[0] == dense.Sizing(12_345, False, True, 5_000_000, 0)
    assert dense.choose(got[0], q.leg, T) == dense.choose(await sizer.of(q), q.leg, T)
    assert _Db.runs == 1 and not sizer._inflight                    # and the later query is a cache hit
