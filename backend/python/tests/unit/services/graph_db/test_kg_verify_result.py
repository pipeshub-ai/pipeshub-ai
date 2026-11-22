"""The result of a verification keeps a bounded number of examples of each difference, whatever number of rows differ, and still says how many there are."""
from __future__ import annotations

from app.services.graph_db.postgres.kg import verify
from app.services.graph_db.postgres.kg.verify import LIMIT, Result


def test_a_page_of_differences_keeps_examples_up_to_the_limit_and_counts_all_of_them() -> None:
    res = Result()
    verify._compare(res, {f"k{i}": "v" for i in range(5 * LIMIT)}, {f"x{i}": "v" for i in range(3 * LIMIT)})
    assert (len(res.missing), len(res.extra), len(res.changed)) == (LIMIT, LIMIT, 0)
    assert res.summary() == f"expected=0 missing={5 * LIMIT} extra={3 * LIMIT} changed=0" and not res.ok


def test_adding_results_keeps_the_examples_bounded_and_the_counts_exact() -> None:
    a, b = Result(), Result()
    verify._compare(a, {f"k{i}": "1" for i in range(LIMIT + 7)}, {f"k{i}": "2" for i in range(LIMIT + 7)})
    verify._compare(b, {f"k{i}": "1" for i in range(LIMIT + 3)}, {f"k{i}": "2" for i in range(LIMIT + 3)})
    both = a + b
    assert len(both.changed) == LIMIT and both.summary() == f"expected=0 missing=0 extra=0 changed={2 * LIMIT + 10}"
