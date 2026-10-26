"""Snowflake column profiling: type classes, the scan, and the rules applied."""

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.sources.external.snowflake.profiling import (
    EXACT_COLUMN_STATS_MAX_CELLS,
    MAX_COLUMNS_PER_TEST,
    SnowflakeProfiler,
    column_kind,
    is_countable,
    numeric_text,
    plan_scan,
    scan_statement,
    top_value_pairs,
    top_value_rows,
    top_values_excluded,
)
from app.sources.external.snowflake.statements import (
    SnowflakeColumn,
    SnowflakeFetchError,
    SnowflakeResult,
)


@dataclass(frozen=True)
class Col:
    name: str
    data_type: str | None = "TEXT"
    is_primary_key: bool = False
    is_unique: bool = False
    is_part_of_composite_primary_key: bool = False


def result_of(row: dict[str, Any]) -> SnowflakeResult:
    # Unquoted aliases come back upper-cased, as from the SQL API.
    return SnowflakeResult(
        columns=[SnowflakeColumn(name.upper(), "text") for name in row], rows=[list(row.values())],
    )


class FakeRunner:
    def __init__(self, *answers: Any) -> None:  # noqa: ANN401
        self.answers = list(answers)
        self.statements: list[str] = []

    async def run(self, statement: str, **_: Any) -> SnowflakeResult:  # noqa: ANN401
        self.statements.append(statement)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestTypes:
    @pytest.mark.parametrize(("data_type", "kind"), [
        ("NUMBER", "numeric"), ("NUMBER(38,0)", "numeric"), ("float", "numeric"),
        ("DOUBLE PRECISION", "numeric"), ("DATE", "temporal"), ("TIMESTAMP_NTZ(9)", "temporal"),
        ("TEXT", "text"), ("varchar(255)", "text"), ("BOOLEAN", None), ("VARIANT", None), (None, None),
    ])
    def test_kind(self, data_type, kind) -> None:
        assert column_kind(data_type) == kind

    @pytest.mark.parametrize(("data_type", "countable", "excluded"), [
        ("TEXT", True, False), ("BOOLEAN", True, False), ("BINARY", True, True),
        ("DECFLOAT", True, True), ("VARIANT", False, True), ("OBJECT", False, True),
        ("ARRAY", False, True), ("GEOGRAPHY", False, True), ("VECTOR(FLOAT, 3)", False, True),
    ])
    def test_countable_and_excluded(self, data_type, countable, excluded) -> None:
        assert is_countable(data_type) is countable
        assert top_values_excluded(data_type) is excluded


COLUMNS = [
    Col("ID", "NUMBER", is_primary_key=True, is_unique=True),
    Col("STATUS", "TEXT"),
    Col("ZIP", "TEXT"),
    Col("EMAIL", "TEXT"),
    Col("CREATED", "DATE"),
    Col("AMOUNT", "DECFLOAT"),
    Col("PAYLOAD", "VARIANT"),
]


class TestPlanAndStatement:
    def test_plan(self) -> None:
        plan = plan_scan(COLUMNS, with_values=True)
        assert plan.counted == ["ID", "STATUS", "ZIP", "EMAIL", "CREATED", "AMOUNT"]
        assert plan.ranged == ["ID", "CREATED", "AMOUNT"]
        assert plan.text == ["STATUS", "ZIP", "EMAIL"]
        # Not the primary key, not personal-looking, not DECFLOAT or VARIANT.
        assert plan.candidates == ["STATUS", "ZIP", "CREATED"]

    def test_without_values_only_counts(self) -> None:
        plan = plan_scan(COLUMNS, with_values=False)
        assert (plan.ranged, plan.text, plan.candidates) == ([], [], [])

    def test_personal_columns_get_no_range(self) -> None:
        plan = plan_scan([Col("DATE_OF_BIRTH", "DATE"), Col("SALARY", "NUMBER")], with_values=True)
        assert plan.ranged == []

    def test_each_test_is_capped(self) -> None:
        plan = plan_scan([Col(f"C{i}") for i in range(MAX_COLUMNS_PER_TEST + 5)], with_values=True)
        assert len(plan.text) == len(plan.candidates) == MAX_COLUMNS_PER_TEST

    def test_statement(self) -> None:
        plan = plan_scan([Col('a"b', "TEXT"), Col("D", "DATE")], with_values=True)
        sql = scan_statement('"DB"."S"."T"', plan, exact=True)
        assert sql == (
            'SELECT COUNT(*) AS total_rows, '
            'COUNT(DISTINCT "a""b") AS d0, COUNT(*) - COUNT("a""b") AS n0, '
            'COUNT(DISTINCT "D") AS d1, COUNT(*) - COUNT("D") AS n1, '
            'MIN("D") AS mn0, MAX("D") AS mx0, '
            'COUNT(TRIM("a""b")) AS tc0, '
            "COUNT_IF(NOT REGEXP_LIKE(TRIM(\"a\"\"b\"), '[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?')) AS nt0, "
            "COUNT_IF(REGEXP_LIKE(TRIM(\"a\"\"b\"), '0[0-9].*')) AS lz0, "
            'APPROX_TOP_K("a""b", 21, 1000) AS k0, COUNT("a""b") AS t0, '
            'APPROX_TOP_K("D", 21, 1000) AS k1, COUNT("D") AS t1 '
            'FROM "DB"."S"."T"'
        )

    def test_no_casts_to_text(self) -> None:
        sql = scan_statement("t", plan_scan(COLUMNS, with_values=True), exact=False)
        assert "::VARCHAR" not in sql and "TO_VARCHAR" not in sql
        assert 'APPROX_COUNT_DISTINCT("ID") AS d0' in sql


class TestParsing:
    def test_pairs_from_json(self) -> None:
        raw = json.dumps([["OPEN", 5], [None, 9], [3, 2], ["bad"], ["X", "n/a"]])
        assert top_value_pairs(raw) == [("OPEN", 5), ("3", 2)]

    @pytest.mark.parametrize("raw", [None, "not json", json.dumps({"a": 1})])
    def test_unreadable_pairs(self, raw) -> None:
        assert top_value_pairs(raw) == []

    def test_rows_drop_singletons(self) -> None:
        assert top_value_rows([("A", 3), ("B", 1)], "10") == [{"value": "A", "n": 3, "total": 10}]

    @pytest.mark.parametrize("total", [None, 0, "x"])
    def test_rows_without_a_total(self, total) -> None:
        assert top_value_rows([("A", 3)], total) == []

    @pytest.mark.parametrize(("args", "expected"), [
        ((10, 0, 0), True), ((10, 1, 0), False), ((10, 0, 1), False), ((0, 0, 0), None), ((None, 0, 0), None),
    ])
    def test_numeric_text(self, args, expected) -> None:
        assert numeric_text(*args) is expected


def scan_row(**values: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"total_rows": 100, **values}


class TestFreshnessAndBudget:
    def test_a_profile_is_current_only_well_after_the_change(self) -> None:
        from app.sources.external.snowflake.profiling import (
            PROFILE_FRESHNESS_MARGIN_MS,
            profile_is_current,
        )
        assert profile_is_current(1000 + PROFILE_FRESHNESS_MARGIN_MS, 1000)
        assert not profile_is_current(999 + PROFILE_FRESHNESS_MARGIN_MS, 1000)
        assert not profile_is_current(None, 1000)
        assert not profile_is_current(10**13, None)

    def test_the_budget_runs_from_the_first_scan(self) -> None:
        from app.sources.external.snowflake.profiling import (
            PROFILING_TIME_BUDGET_S,
            ProfilingBudget,
        )
        now = [0.0]
        budget = ProfilingBudget(clock=lambda: now[0])
        now[0] = PROFILING_TIME_BUDGET_S * 10
        assert not budget.exhausted
        budget.start()
        now[0] += PROFILING_TIME_BUDGET_S + 1
        assert budget.exhausted

    def test_only_warehouse_trouble_counts_toward_stopping(self) -> None:
        from app.sources.external.snowflake.profiling import ProfilingBudget
        budget = ProfilingBudget()
        for _ in range(5):
            budget.record_failure(SnowflakeFetchError("timed out", sqlstate="57014"))
            budget.record_failure(SnowflakeFetchError("no such table", sqlstate="42S02"))
        assert not budget.exhausted
        for _ in range(3):
            budget.record_failure(SnowflakeFetchError("suspended", sqlstate="57P03"))
        assert budget.exhausted
        budget.record_success()
        assert not budget.exhausted


class TestProfileTable:
    async def profile(self, runner, *, row_count=100, with_values=True, columns=COLUMNS) -> object:
        return await SnowflakeProfiler(runner).profile_table(
            "DB", "S", "T", columns, row_count=row_count, with_values=with_values,
        )

    async def test_counts_ranges_numeric_text_and_screened_values(self) -> None:
        runner = FakeRunner(result_of(scan_row(
            d0=100, n0=0, d1=3, n1=0, d2=4, n2=0, d3=80, n3=20, d4=50, n4=0, d5=5, n5=0,
            mn0=1, mx0=100, mn1="2024-01-01", mx1="2024-12-31", mn2="1.5", mx2="9.5",
            tc0=100, nt0=100, lz0=0, tc1=100, nt1=0, lz1=3, tc2=80, nt2=80, lz2=0,
            k0=json.dumps([["OPEN", 60], ["CLOSED", 30], ["HOLD", 10]]), t0=100,
            k1=json.dumps([["02134", 50], ["90210", 50]]), t1=100,
            k2=json.dumps([["2024-01-01", 2]]), t2=100,
        )))
        result = await self.profile(runner)
        assert len(runner.statements) == 1
        assert result.row_count == 100
        assert result.stats["STATUS"]["distinct_count"] == 3
        assert "PAYLOAD" not in result.stats
        status, zip_code, email = (result.profiles[n] for n in ("STATUS", "ZIP", "EMAIL"))
        assert status.top_values == ["OPEN", "CLOSED", "HOLD"]
        assert status.top_value_frequencies == [0.6, 0.3, 0.1]
        assert status.numeric_text is False
        assert zip_code.numeric_text is False
        assert zip_code.top_values == ["02134", "90210"]
        assert email.top_values is None and email.numeric_text is False
        # The decoded value, as text; no session-dependent cast in SQL.
        assert (result.profiles["ID"].min_value, result.profiles["ID"].max_value) == ("1", "100")
        assert result.profiles["CREATED"].max_value == "2024-12-31"
        assert result.profiles["PAYLOAD"].profiled_at is not None

    async def test_a_column_the_counts_rule_out_keeps_no_values(self) -> None:
        runner = FakeRunner(result_of(scan_row(
            d0=90, n0=0, k0=json.dumps([["a", 2], ["b", 2]]), t0=100, tc0=100, nt0=100, lz0=0,
        )))
        result = await self.profile(runner, columns=[Col("CODE", "TEXT")])
        assert result.profiles["CODE"].top_values is None

    async def test_exact_counts_under_the_budget_and_approximate_over_it(self) -> None:
        under = FakeRunner(result_of(scan_row()))
        await self.profile(under, row_count=10)
        assert "COUNT(DISTINCT" in under.statements[0]
        over = FakeRunner(result_of(scan_row()))
        await self.profile(over, row_count=EXACT_COLUMN_STATS_MAX_CELLS)
        assert "APPROX_COUNT_DISTINCT" in over.statements[0]

    async def test_without_values_one_statement_reads_no_values(self) -> None:
        runner = FakeRunner(result_of(scan_row(d0=10, n0=0)))
        result = await self.profile(runner, with_values=False)
        # Only when it was taken: no values, no extremes, no numeric-text test.
        assert all(p.profiled_at and p.model_dump(exclude={"profiled_at"}, exclude_none=True) == {}
                   for p in result.profiles.values())
        (statement,) = runner.statements
        for value_reading in ("MIN(", "MAX(", "APPROX_TOP_K", "REGEXP_LIKE"):
            assert value_reading not in statement

    async def test_a_personal_text_column_is_never_top_k_read(self) -> None:
        runner = FakeRunner(result_of(scan_row()))
        await self.profile(runner, columns=[Col("CUSTOMER_EMAIL"), Col("FIRST_NAME")])
        assert "APPROX_TOP_K" not in runner.statements[0]

    async def test_a_refused_scan_raises(self) -> None:
        runner = FakeRunner(SnowflakeFetchError("warehouse suspended", sqlstate="57P03"))
        with pytest.raises(SnowflakeFetchError):
            await self.profile(runner)

    @pytest.mark.parametrize("answer", [
        SnowflakeResult(columns=[], rows=[]),
        SnowflakeResult(columns=[SnowflakeColumn("D0", "fixed", 0)], rows=[[1]]),
    ])
    async def test_a_result_without_a_row_count_is_a_failure(self, answer) -> None:
        with pytest.raises(SnowflakeFetchError):
            await self.profile(FakeRunner(answer))
