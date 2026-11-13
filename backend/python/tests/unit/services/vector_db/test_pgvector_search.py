"""The pure parts of the pgvector search path: the tier chooser, fusion, the knobs and the membership fragments."""

from __future__ import annotations

import dataclasses

import pytest

from app.services.vector_db.pgvector import dense, fusion, validate
from app.services.vector_db.pgvector.errors import PgVectorError
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


# ---- the organisation in every statement, and the statements that are built --------------------------------------------------------------
ORG = "a" * 24


def query(scope: Scope | None = None, metric: str = "cosine") -> dense.Query:
    return dense.Query(ORG, 1, 1, 8, metric, "[1]", scope or Scope(["c"], ["g"], [], ["r"], ["m"]), 10)


def test_every_statement_of_the_dense_path_pins_the_org_the_collection_and_the_space() -> None:
    points = {
        "exact": dense.exact_sql(query()),
        "exact, whole org": dense.exact_sql(query(Scope())),
        "index": dense.index_sql(query()),
        "bounded, narrow": dense.bounded_sql(query(), narrow=True),
        "bounded, by probe": dense.bounded_sql(query(), narrow=False),
        "count": dense.count_sql(query()),
    }
    for name, sql in points.items():
        assert "p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3" in sql, name
        assert "t.org_id = $1" in sql or "count(*)" in sql, name                       # the tombstone test is the org's own
    from app.services.vector_db.pgvector import lexical

    assert "p.org_id = $1 AND p.point_sk = s.point_sk" in lexical.lexical_sql("")                 # the function takes the org from the unit of work; the join pins it again
    assert "p.org_id = $1" in lexical.lexical_sql(" AND (p.ent_type = ANY($12::text[]))")
    for sql in (dense._TOMBSTONES, dense._CONNECTORS, dense._UNCONNECTED, dense._LEAF, validate._BOUNDS, validate._NEXT):
        assert "org_id = $1" in sql, sql[:60]
    # every table reference carries its own alias-qualified predicate (a substring check would pass with one of several removed)
    assert dense._TOMBSTONES.count("t.org_id = $1") == 1 and dense._LEAF.count("p.org_id = $1") == 1
    assert dense._UNCONNECTED.count("p.org_id = $1") == 1 and dense._UNCONNECTED.count("m.org_id = $1") == 1
    assert dense._CONNECTORS.count("m.org_id = $1 AND m.collection_id = $2") == 2 and dense._CONNECTORS.count("org_id") == 2
    for sql in (validate._BOUNDS, validate._NEXT):
        assert "p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3" in sql
    assert "m.org_id = $1" in dense.exact_sql(query()) and "m.collection_id = $2" in dense.exact_sql(query())     # the membership probe too
    for sql in (validate._LOAD, validate._SAVE, validate._FAIL):
        assert "org_id" in sql and "$1" in sql


def test_the_admission_statement_uses_the_registered_lock_class_and_an_unknown_metric_is_refused() -> None:
    assert f"pg_try_advisory_xact_lock({dense.ADMISSION_CLASS}," in dense._ADMIT
    with pytest.raises(PgVectorError, match="metric"):
        dense.exact_sql(query(metric="euclid"))


def _built_statements(source: str) -> dict[str, int]:
    """Statement texts built from parts in ``source``, by the function that builds them: an f-string with values, ``"..." % x``,
    ``"..." + x`` and ``"...".format(x)`` whose constant text holds a SQL keyword."""
    import ast
    import re

    keywords = re.compile(r"\bselect\b|\binsert\s+into\b|\bupdate\s+\S+\s+set\b|\bdelete\s+from\b", re.I)
    tree = ast.parse(source)
    owner: dict[ast.AST, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for n in ast.walk(fn):
                owner[n] = fn.name
    found: dict[str, int] = {}
    for n in ast.walk(tree):
        text, built = "", False
        if isinstance(n, ast.JoinedStr):
            built = any(isinstance(v, ast.FormattedValue) for v in n.values)
            text = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod | ast.Add) and isinstance(n.left, ast.Constant):      # "..." % x, "..." + x
            built = not isinstance(n.right, ast.Constant)
            text = n.left.value if isinstance(n.left.value, str) else ""
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "format" and isinstance(n.func.value, ast.Constant):
            built, text = True, str(n.func.value.value)                                                                    # "...".format(x)
        if built and keywords.search(text):
            found[owner.get(n, "<module>")] = found.get(owner.get(n, "<module>"), 0) + 1
    return found


def test_the_detector_of_built_statements_sees_every_way_of_building_one() -> None:
    assert _built_statements('def f(x):\n    return f"SELECT {x}"') == {"f": 1}
    assert _built_statements('def f(x):\n    return "SELECT %s" % x') == {"f": 1}
    assert _built_statements('def f(x):\n    return "SELECT * FROM t WHERE a = " + x') == {"f": 1}
    assert _built_statements('def f(x):\n    return "SELECT {}".format(x)') == {"f": 1}
    assert _built_statements('def f(x):\n    return "SELECT 1 " + "FROM t"') == {}          # constants joined are not built


def test_the_statements_the_search_builds_from_parts_are_exactly_these() -> None:
    """Every statement text of the search path that is built from parts, by file and function. A statement built anywhere else in these
    modules fails here (the shared SQL guard sees only a statement built where it is passed to ``fetch``)."""
    from pathlib import Path

    root = Path(dense.__file__).parent
    found: dict[tuple[str, str], int] = {}
    for path in sorted(root.glob("*.py")):
        if path.name in ("writes.py", "reads.py"):          # their built statements are pinned by test_pgvector_writes.py
            continue
        for fn, count in _built_statements(path.read_text()).items():
            found[(path.name, fn)] = count
    # the four statements of the dense path, and the fragments they and the filters are made of (membership probes and the payload
    # conditions and the scope filter's condition in its two forms, admitting and deferring: fixed text, every value a ``$n``)
    assert found == {("dense.py", "exact_sql"): 1, ("dense.py", "index_sql"): 1, ("dense.py", "bounded_sql"): 1, ("dense.py", "count_sql"): 1,
                     ("lexical.py", "lexical_sql"): 1, ("scope_filter.py", "predicate"): 2, ("sql_filters.py", "_match"): 2,
                     ("visibility.py", "candidates"): 1, ("visibility.py", "probe"): 1}, found


@pytest.mark.asyncio
async def test_exact_leaves_the_statement_budget_to_the_unit_of_work() -> None:
    """The sampler and the check run under ``timeout_ms`` of the unit of work, which never exceeds the org's own limit; setting
    ``statement_timeout`` again inside could raise it."""
    seen: list[str] = []

    class Tx:
        async def fetchval(self, sql: str, *args: object) -> int:
            seen.append(sql)
            return 1

        async def fetch(self, sql: str, *args: object) -> list:
            seen.append(sql)
            return []

    class Db:
        async def run(self, fn, **opts: object) -> object:
            seen.append(f"timeout_ms={opts['timeout_ms']}")
            return await fn(Tx())

    assert await dense.exact(Db(), query(), timeout_ms=5000, t=T) == []
    assert not any("statement_timeout" in s for s in seen) and "timeout_ms=5000" in seen


@pytest.mark.asyncio
async def test_a_check_stops_taking_samples_after_its_deadline_and_never_sets_the_statement_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    rows = iter(range(1000))

    class Tx:
        async def fetchval(self, sql: str, *args: object) -> int:
            seen.append(sql)
            return 1

        async def fetchrow(self, sql: str, *args: object) -> object:
            seen.append(sql)
            return (1, 100) if "min(" in sql else {"point_sk": next(rows), "v": "[1]"}

        async def fetch(self, sql: str, *args: object) -> list:
            return []

    class Db:
        async def run(self, fn, **opts: object) -> object:
            assert opts["timeout_ms"] == 60_000                      # per statement, and never above the org's limit
            return await fn(Tx())

    assert await validate.bit_recall(Db(), T, ORG, 1, 1, 8, "cosine", samples=5) is None          # no exact neighbours: no verdict, 5 samples read
    assert sum("check_next" in s for s in seen) == 5 and not any("statement_timeout" in s for s in seen)
    seen.clear()
    monkeypatch.setattr(validate, "CHECK_DEADLINE_S", -1)
    await validate.bit_recall(Db(), T, ORG, 1, 1, 8, "cosine", samples=50)
    assert not any("check_next" in s for s in seen)                                                   # the deadline had passed: no sample started


@pytest.mark.asyncio
async def test_connector_checks_that_miss_together_share_one_read() -> None:
    import asyncio

    from app.core import org_context

    reads_made: list[int] = []

    class Db:
        async def run(self, fn, **opts: object) -> object:
            reads_made.append(1)
            await asyncio.sleep(0.05)

            class Tx:
                async def fetch(self, sql: str, *a: object) -> list:
                    return [{"member_id": "c1"}]

                async def fetchval(self, sql: str, *a: object) -> bool:
                    return False

            return await fn(Tx())

    sizer = dense.Sizer(Db(), T)
    with org_context.bind(org_context.OrgContext(ORG, None, "service", "t", "-")):
        got = await asyncio.gather(*[sizer.connectors(ORG, 1) for _ in range(8)])
        assert all(g == frozenset({"c1"}) for g in got) and len(reads_made) == 1
        await sizer.connectors(ORG, 1)
        assert len(reads_made) == 1                                                   # and the answer is reused for its ttl


# ---- the lexical leg -------------------------------------------------------------------------------------------------------------------
def test_lexical_terms_are_distinct_unsigned_32_bit_ids_with_finite_weights() -> None:
    from app.services.vector_db.models import SparseVector
    from app.services.vector_db.pgvector import lexical

    assert lexical.terms_of(SparseVector([3, 3, 4294967295], [1.0, 2.0, 0.5])) == ([3, 4294967295], [2.0, 0.5])     # a repeated id keeps its last weight
    for bad in (SparseVector([], []), SparseVector([-1], [1.0]), SparseVector([2**32], [1.0]), SparseVector([1], [float("nan")]),
                SparseVector(list(range(lexical.MAX_TERMS + 1)), [1.0] * (lexical.MAX_TERMS + 1))):
        with pytest.raises(ValueError):
            lexical.terms_of(bad)


def test_a_lexical_leg_asks_for_more_rows_only_when_the_extra_conditions_filter_them_afterwards() -> None:
    from app.services.vector_db.models import FieldCondition, FilterExpression
    from app.services.vector_db.pgvector import lexical

    assert lexical.rows_asked(40, Scope(), T) == 40
    extra = Scope(extra=FilterExpression(must=[FieldCondition(key="metadata.entityType", value="x")]))
    assert lexical.rows_asked(40, extra, T) == 40 * T.lexical_overfetch
    assert lexical.rows_asked(900, extra, T) == lexical.MAX_LEG


class _LexTx:
    """A fake transaction behind ``lexical._run``: the function's raw rows (``total`` of them, best first), ``ok`` for the ones the extra
    conditions admit; records the arguments of every statement."""

    def __init__(self, total: int, ok: object) -> None:
        self.total, self.ok, self.calls = total, ok, []

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self.calls.append((sql, args))
        asked = args[9]                                                     # $10
        return [{"point_sk": i, "point_id": f"id{i}", "ok": self.ok(i)} for i in range(min(asked, self.total))]


class _LexDb:
    def __init__(self, tx: _LexTx | None = None, raises: Exception | None = None) -> None:
        self.tx, self.raises, self.opts = tx, raises, []

    async def run(self, fn: object, **opts: object) -> object:
        self.opts.append(opts)
        if self.raises:
            raise self.raises
        return await fn(self.tx)


def _extra() -> Scope:
    from app.services.vector_db.models import FieldCondition, FilterExpression

    return Scope(extra=FilterExpression(must=[FieldCondition(key="metadata.entityType", value="x")]))


async def test_the_lexical_statement_gets_the_org_the_collection_the_terms_and_the_overfetched_row_count() -> None:
    from app.services.vector_db.models import SparseVector
    from app.services.vector_db.pgvector import lexical

    tx = _LexTx(1000, lambda i: True)
    rows, degraded = await lexical.search(_LexDb(tx), ORG, 7, SparseVector([4, 5], [1.0, 2.0]), _extra(), 20, T)
    (sql, args), = tx.calls
    assert args[:4] == (ORG, 7, [4, 5], [1.0, 2.0]) and args[9] == 20 * T.lexical_overfetch      # the org, not only the SQL text; the overfetch, not ``leg``
    assert len(rows) == 20 and degraded is None
    tx = _LexTx(1000, lambda i: True)
    await lexical.search(_LexDb(tx), ORG, 7, SparseVector([4], [1.0]), Scope(), 20, T)
    assert tx.calls[0][1][9] == 20                                          # no extra conditions: no overfetch


async def test_a_selective_condition_pages_further_into_the_function_rows_and_says_so_when_the_bound_is_reached() -> None:
    from app.services.vector_db.models import SparseVector
    from app.services.vector_db.pgvector import lexical

    sparse = SparseVector([4], [1.0])
    tx = _LexTx(5000, lambda i: i in (300, 301, 302))                          # the matches are far down the function's order
    rows, degraded = await lexical.search(_LexDb(tx), ORG, 7, sparse, _extra(), 3, T)
    assert [r[0] for r in rows] == [300, 301, 302] and degraded is None
    assert [c[1][9] for c in tx.calls] == [12, 48, 192, 768]                  # four times as many each time, until 3 are kept
    tx = _LexTx(5000, lambda i: i in (300, 301, 302))
    rows, degraded = await lexical.search(_LexDb(tx), ORG, 7, sparse, _extra(), 10, T)
    assert [r[0] for r in rows] == [300, 301, 302] and degraded == "partial"  # fewer than wanted, and the function had more past the bound
    tx = _LexTx(5000, lambda i: i in (3000, 3001))                             # beyond the bound
    rows, degraded = await lexical.search(_LexDb(tx), ORG, 7, sparse, _extra(), 10, T)
    assert rows == [] and degraded == "partial" and tx.calls[-1][1][9] == lexical.MAX_LEG
    tx = _LexTx(50, lambda i: i == 7)                                          # the function has nothing more: complete, not partial
    rows, degraded = await lexical.search(_LexDb(tx), ORG, 7, sparse, _extra(), 10, T)
    assert [r[0] for r in rows] == [7] and degraded is None
    tx = _LexTx(5000, lambda i: True)                                          # enough kept in the first statement: one statement
    rows, degraded = await lexical.search(_LexDb(tx), ORG, 7, sparse, _extra(), 10, T)
    assert len(rows) == 10 and len(tx.calls) == 1


@pytest.mark.parametrize(("error", "reason"), [("DbTimeout", "timeout"), ("PoolTimeout", "timeout"), ("ConnectionLost", "error"), ("DbError", "error")])
async def test_a_lexical_leg_that_fails_answers_nothing_and_says_why(error: str, reason: str, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from app.core.db import errors
    from app.services.vector_db.models import SparseVector
    from app.services.vector_db.pgvector import lexical

    exc = errors.ConnectionLost("down", retry_safe=True) if error == "ConnectionLost" else getattr(errors, error)("down")
    caplog.set_level(logging.WARNING)
    rows, degraded = await lexical.search(_LexDb(raises=exc), ORG, 7, SparseVector([4], [1.0]), Scope(), 20, T)
    assert rows == [] and degraded == reason
    assert any(r.levelno >= (logging.ERROR if reason == "error" else logging.WARNING) for r in caplog.records)


def test_a_weight_that_does_not_fit_a_32_bit_float_is_refused() -> None:
    from app.services.vector_db.models import SparseVector
    from app.services.vector_db.pgvector import lexical

    with pytest.raises(ValueError, match="32-bit"):
        lexical.terms_of(SparseVector([1], [1e39]))
    assert lexical.terms_of(SparseVector([1], [3.0e38]))[1] == [3.0e38]


async def test_a_lexical_leg_for_a_filter_that_trusts_nothing_asks_nothing_and_is_complete() -> None:
    from app.services.vector_db.filters import ScopeFilter
    from app.services.vector_db.models import SparseVector
    from app.services.vector_db.pgvector import lexical

    for flt in (ScopeFilter(current=frozenset({"c1"}), version=5, generation=-2), ScopeFilter(current=frozenset(), version=5, generation=3)):
        tx = _LexTx(5000, lambda i: False)                     # what "AND false" would answer: many rows, none admitted
        db = _LexDb(tx)
        rows, degraded = await lexical.search(db, ORG, 7, SparseVector([4], [1.0]), Scope(scope_filter=flt), 20, T)
        assert (rows, degraded, tx.calls, db.opts) == ([], None, [], [])
    live = ScopeFilter(current=frozenset({"c1"}), version=5, generation=3)                 # a filter that can trust something still asks
    tx = _LexTx(5000, lambda i: False)
    rows, degraded = await lexical.search(_LexDb(tx), ORG, 7, SparseVector([4], [1.0]), Scope(scope_filter=live), 20, T)
    assert tx.calls and degraded == "partial"
