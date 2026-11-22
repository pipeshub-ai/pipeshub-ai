"""MariaDB profiling: what is scanned, how it is read, and when it stops."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.mariadb.mariadb_ import ColumnInfo
from app.sources.external.mariadb.profiling import (
    MAX_CONSECUTIVE_UNAVAILABLE,
    PROFILE_STATEMENT_TIMEOUT_S,
    PROFILING_TIME_BUDGET_S,
    TOP_VALUES_MAX_SCANS_PER_TABLE,
    MariaDBProfiler,
    ProfileError,
    ProfilingBudget,
    column_kind,
    is_countable,
    is_ranged,
    plan_scan,
    scan_statement,
)


def col(name, data_type="int", **extra) -> ColumnInfo:
    return ColumnInfo(name=name, data_type=data_type, **extra)


class FakeClient:
    """Answers the scan with ``scan_row`` and each top-values query with ``top_rows``."""

    def __init__(self, scan_row=None, top_rows=None) -> None:
        self.scan_row = scan_row
        self.top_rows = top_rows or []
        self.statements: list[str] = []
        self.timeouts: list[float] = []

    async def execute_read_only_query(self, statement, timeout_s, *, max_rows=None, slots=None) -> tuple:
        self.statements.append(statement)
        self.timeouts.append(timeout_s)
        if statement.startswith("SELECT value, n, total"):
            return ["value", "n", "total"], [tuple(r) for r in self.top_rows], False
        if self.scan_row is None:
            return [], [], False
        return list(self.scan_row), [tuple(self.scan_row.values())], False


class TestTypes:
    @pytest.mark.parametrize(("data_type", "kind", "countable"), [
        ("int", "numeric", True), ("decimal", "numeric", True), ("datetime", "temporal", True),
        ("varchar", "text", True), ("longtext", "text", True), ("enum", "text", True),
        ("blob", None, False), ("varbinary", None, False), ("geometry", None, False), ("bit", None, False),
    ])
    def test_classes(self, data_type, kind, countable) -> None:
        assert column_kind(data_type) == kind and is_countable(data_type) == countable

    def test_labels_are_not_ranged(self) -> None:
        assert not is_ranged(col("status", "enum")) and is_ranged(col("amount", "decimal"))


class TestPlan:
    def test_without_values_only_counts(self) -> None:
        plan = plan_scan([col("id"), col("note", "varchar"), col("pic", "blob")], with_values=False, counts=True)
        assert (plan.counted, plan.ranged, plan.text) == (["id", "note"], [], [])

    def test_over_budget_only_ranges(self) -> None:
        plan = plan_scan([col("amount", "decimal"), col("note", "varchar")], with_values=True, counts=False)
        assert (plan.counted, plan.ranged, plan.text) == ([], ["amount"], [])

    def test_the_scan_quotes_and_anchors(self) -> None:
        sql = scan_statement("`shop`.`t`", plan_scan([col("a`b", "varchar")], with_values=True, counts=True))
        assert "COUNT(DISTINCT `a``b`) AS d0" in sql
        assert "SUM(TRIM(`a``b`) NOT REGEXP '^[+-]?" in sql and "\\" not in sql


class TestProfile:
    async def test_within_the_budget_counts_ranges_numeric_text_and_top_values(self) -> None:
        client = FakeClient(
            scan_row={"total_rows": 100, "d0": 100, "n0": 0, "d1": 3, "n1": 0, "d2": 40, "n2": 10,
                      "mn0": "1", "mx0": "100", "tc0": 100, "nt0": 0, "lz0": 0},
            top_rows=[("paid", 60, 100), ("open", 40, 100)],
        )
        columns = [col("id", is_primary_key=True, is_unique=True), col("status", "varchar"),
                   col("code", "varchar")]
        profile = await MariaDBProfiler(client).profile_table(
            "shop", "orders", columns, row_estimate=100, cardinality={}, with_values=True,
        )
        assert profile.row_count == 100
        assert profile.stats["status"]["distinct_count"] == 3
        assert (profile.profiles["id"].min_value, profile.profiles["id"].max_value) == ("1", "100")
        assert profile.profiles["status"].numeric_text is True
        assert profile.profiles["status"].top_values == ["paid", "open"]
        # Read-only and bounded, every statement.
        assert set(client.timeouts) == {PROFILE_STATEMENT_TIMEOUT_S}
        # The key is no vocabulary: one top-values scan, for status (code: 40 distinct of 90 values).
        assert sum(s.startswith("SELECT value") for s in client.statements) <= 2

    async def test_over_the_budget_index_cardinality_stands_in_and_no_top_values(self) -> None:
        client = FakeClient(scan_row={"total_rows": 0, "mn0": "1", "mx0": "9"})
        profile = await MariaDBProfiler(client).profile_table(
            "shop", "events", [col("id"), col("kind", "varchar")], row_estimate=10_000_000,
            cardinality={"kind": 12}, with_values=True,
        )
        assert profile.stats == {"kind": {"name": "kind", "ordinal_position": 1, "distinct_count": 12,
                                          "null_count": None, "cardinality_ratio": None}}
        assert "COUNT(DISTINCT" not in client.statements[0]
        assert not any(s.startswith("SELECT value") for s in client.statements)

    async def test_a_scan_that_answers_nothing_fails_the_profile(self) -> None:
        # The async pool answered a statement stopped by max_statement_time with no rows.
        with pytest.raises(ProfileError):
            await MariaDBProfiler(FakeClient(scan_row=None)).profile_table(
                "shop", "t", [col("id")], row_estimate=10, cardinality={}, with_values=True,
            )

    async def test_a_failed_statement_raises(self) -> None:
        client = MagicMock()
        client.execute_read_only_query = AsyncMock(side_effect=TimeoutError("1969"))
        with pytest.raises(TimeoutError):
            await MariaDBProfiler(client).profile_table(
                "shop", "t", [col("id")], row_estimate=10, cardinality={}, with_values=True,
            )

    async def test_top_values_scans_are_capped_per_table(self) -> None:
        columns = [col(f"c{i}", "varchar") for i in range(15)]
        row = {"total_rows": 100}
        for i in range(15):
            row.update({f"d{i}": 3, f"n{i}": 0, f"tc{i}": 100, f"nt{i}": 100, f"lz{i}": 0})
        client = FakeClient(scan_row=row, top_rows=[("a", 50, 100)])
        await MariaDBProfiler(client).profile_table(
            "shop", "t", columns, row_estimate=100, cardinality={}, with_values=True,
        )
        assert sum(s.startswith("SELECT value") for s in client.statements) == TOP_VALUES_MAX_SCANS_PER_TABLE

    async def test_without_values_no_value_is_read(self) -> None:
        client = FakeClient(scan_row={"total_rows": 5, "d0": 2, "n0": 0})
        profile = await MariaDBProfiler(client).profile_table(
            "shop", "t", [col("status", "varchar")], row_estimate=5, cardinality={}, with_values=False,
        )
        assert profile.stats["status"]["distinct_count"] == 2
        assert profile.profiles["status"].top_values is None and len(client.statements) == 1

    async def test_personal_looking_columns_get_no_top_values(self) -> None:
        client = FakeClient(scan_row={"total_rows": 100, "d0": 3, "n0": 0, "tc0": 100, "nt0": 100, "lz0": 0},
                            top_rows=[("a@x", 50, 100)])
        await MariaDBProfiler(client).profile_table(
            "shop", "t", [col("email", "varchar")], row_estimate=100, cardinality={}, with_values=True,
        )
        assert not any(s.startswith("SELECT value") for s in client.statements)


class TestBudget:
    def test_three_unavailable_in_a_row_exhaust_it(self) -> None:
        budget = ProfilingBudget()
        for _ in range(MAX_CONSECUTIVE_UNAVAILABLE):
            budget.record_failure(TimeoutError())
        assert budget.exhausted

    def test_a_success_or_another_failure_resets_the_run(self) -> None:
        budget = ProfilingBudget()
        budget.record_failure(TimeoutError())
        budget.record_failure(TimeoutError())
        budget.record_success()
        budget.record_failure(TimeoutError())
        assert not budget.exhausted
        budget.record_failure(ValueError("bad sql"))
        assert not budget.exhausted

    def test_time_runs_out(self) -> None:
        now = [0.0]
        budget = ProfilingBudget(clock=lambda: now[0])
        budget.start()
        now[0] = PROFILING_TIME_BUDGET_S + 1
        assert budget.exhausted


class TestReviewFixes:
    def test_a_statement_timeout_is_not_unavailability(self) -> None:
        class StatementTimeout(Exception):
            errno = 1969

        budget = ProfilingBudget()
        for _ in range(5):
            budget.record_failure(StatementTimeout())
        assert not budget.exhausted

    async def test_addresses_get_no_top_values(self) -> None:
        client = FakeClient(scan_row={"total_rows": 100, "d0": 3, "n0": 0, "tc0": 100, "nt0": 100, "lz0": 0},
                            top_rows=[("10.1.2.3", 50, 100)])
        await MariaDBProfiler(client).profile_table(
            "shop", "t", [col("src", "inet4")], row_estimate=100, cardinality={}, with_values=True,
        )
        assert not any(s.startswith("SELECT value") for s in client.statements)

    async def test_a_zero_row_estimate_is_no_estimate(self) -> None:
        client = FakeClient(scan_row={"total_rows": 0})
        profile = await MariaDBProfiler(client).profile_table(
            "shop", "t", [col("id"), col("amount", "decimal")], row_estimate=0, cardinality={"id": 9},
            with_values=True,
        )
        assert client.statements == []  # no exact scan, no range scan of unknown size
        assert profile.stats["id"]["distinct_count"] == 9
