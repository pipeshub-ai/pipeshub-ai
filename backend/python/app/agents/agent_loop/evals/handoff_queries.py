"""Query set for `handoff_comparison.py`: ordinary delegation vs a delegate
answering the user directly (`PIPESHUB_DELEGATE_HANDOFF`).

`expect_final` is what a well-behaved model should do with the `final` flag:
`True` when one delegate's output is already the whole answer, `False` when
the root agent still has work to do after the delegate (combine, compute,
advise) or nothing to delegate at all, `None` when either choice is
defensible. The harness reports how often the model's choice matched it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

QueryCategory = Literal["handoff_candidate", "should_not_handoff", "ambiguous", "control"]


class HandoffQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    category: QueryCategory
    prompt: str
    expect_final: bool | None
    needs_code: bool = False
    needs_web: bool = False


HANDOFF_QUERIES: tuple[HandoffQuery, ...] = (
    HandoffQuery(
        id="fib_sum",
        category="handoff_candidate",
        prompt=(
            "Use code to compute the first 25 Fibonacci numbers, then tell me their sum "
            "and the ratio of the last two."
        ),
        expect_final=True, needs_code=True,
    ),
    HandoffQuery(
        id="revenue_chart",
        category="handoff_candidate",
        prompt=(
            "Make a bar chart (PNG) of quarterly revenue in $k: Q1 120, Q2 150, Q3 135, Q4 180. "
            "Then say which quarter grew the most over the previous one."
        ),
        expect_final=True, needs_code=True,
    ),
    HandoffQuery(
        id="times_table_csv",
        category="handoff_candidate",
        prompt="Create a CSV file with the multiplication table from 1 to 10 and tell me what it contains.",
        expect_final=True, needs_code=True,
    ),
    HandoffQuery(
        id="prime_stats",
        category="handoff_candidate",
        prompt=(
            "Using code, find all primes below 10,000. Report how many there are, "
            "the largest twin-prime pair, and the average gap between consecutive primes."
        ),
        expect_final=True, needs_code=True,
    ),
    HandoffQuery(
        id="web_python_release",
        category="handoff_candidate",
        prompt="Search the web: what is the latest stable Python release and what are its headline new features? Cite sources.",
        expect_final=True, needs_web=True,
    ),
    HandoffQuery(
        id="web_world_cup",
        category="handoff_candidate",
        prompt="Find out from the web who won the most recent men's FIFA World Cup, the final score, and where it was played. Cite sources.",
        expect_final=True, needs_web=True,
    ),
    HandoffQuery(
        id="web_then_compute_ratio",
        category="should_not_handoff",
        prompt=(
            "Search the web for the current populations of Japan and Germany, then use code to compute "
            "their ratio and how much larger Japan is in percent. Summarise the sources and the math together."
        ),
        expect_final=False, needs_code=True, needs_web=True,
    ),
    HandoffQuery(
        id="fx_convert_table",
        category="should_not_handoff",
        prompt=(
            "Get the current EUR to USD exchange rate from the web, then use code to convert the prices "
            "19.99, 249 and 1299.50 EUR to USD. Show a table and name the rate's source."
        ),
        expect_final=False, needs_code=True, needs_web=True,
    ),
    HandoffQuery(
        id="mortgage_advice",
        category="ambiguous",
        prompt=(
            "Compute with code the monthly payment for a $350,000 30-year mortgage at 6.5% and at 7.5%, "
            "then advise me in three bullet points whether waiting for lower rates is wise."
        ),
        expect_final=None, needs_code=True,
    ),
    HandoffQuery(
        id="web_sqlite_vs_duckdb",
        category="ambiguous",
        prompt=(
            "Research on the web the pros and cons of SQLite versus DuckDB for analytics, "
            "then recommend one for a 50 GB dataset on a laptop."
        ),
        expect_final=None, needs_web=True,
    ),
    HandoffQuery(
        id="chitchat",
        category="control",
        prompt="Hi! Thanks for the help earlier. How is your day going?",
        expect_final=False,
    ),
    HandoffQuery(
        id="definition",
        category="control",
        prompt="In two sentences, what is the difference between a process and a thread?",
        expect_final=False,
    ),
)


def select_queries(ids: list[str] | None) -> tuple[HandoffQuery, ...]:
    if not ids:
        return HANDOFF_QUERIES
    by_id = {q.id: q for q in HANDOFF_QUERIES}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ValueError(f"unknown query id(s) {unknown}; available: {sorted(by_id)}")
    return tuple(by_id[i] for i in ids)
