"""Tests for column value profiling in app.sources.external.postgres.postgres_."""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.postgres.postgres_ import (
    MIN_MAX_SCAN_MAX_CELLS,
    TOP_VALUE_MAX_CHARS,
    TOP_VALUES_LIMIT,
    TOP_VALUES_MAX_SCANS_PER_TABLE,
    TOP_VALUES_SCAN_MAX_ROWS,
    ColumnInfo,
    ColumnProfile,
    PgColumnStatistics,
    PostgreSQLDataSource,
    TableColumnStatistics,
    countable_column_names,
    detect_numeric_text,
    estimate_distinct_and_ratio,
    looks_personal,
    parse_pg_array_literal,
    screen_top_values,
    top_values_eligible,
    top_values_from_counts,
    top_values_from_pg_stats,
)


def _data_source(execute: AsyncMock | Callable[..., Awaitable[list]]) -> PostgreSQLDataSource:
    client = MagicMock()
    client.execute_query = execute if isinstance(execute, AsyncMock) else AsyncMock(side_effect=execute)
    return PostgreSQLDataSource(client)


def _queries(ds: PostgreSQLDataSource) -> list[str]:
    return [call.args[0] for call in ds.get_client().execute_query.await_args_list]


class TestParsePgArrayLiteral:

    def test_plain_elements(self) -> None:
        assert parse_pg_array_literal("{h,c,o,cl}") == ["h", "c", "o", "cl"]

    def test_quoted_elements_keep_commas_and_spaces(self) -> None:
        assert parse_pg_array_literal('{SLE,"SLE, SJS","POPLATEK MESICNE"}') == [
            "SLE", "SLE, SJS", "POPLATEK MESICNE",
        ]

    def test_escaped_quotes_and_backslashes(self) -> None:
        assert parse_pg_array_literal(r'{"say \"hi\"","C:\\temp","{braces}"}') == [
            'say "hi"', "C:\\temp", "{braces}",
        ]

    def test_null_versus_the_string_null(self) -> None:
        assert parse_pg_array_literal('{a,NULL,"NULL",null}') == ["a", None, "NULL", None]

    def test_empty_string_and_empty_array(self) -> None:
        assert parse_pg_array_literal('{"",x}') == ["", "x"]
        assert parse_pg_array_literal("{}") == []

    def test_control_characters_stay_in_unquoted_elements(self) -> None:
        assert parse_pg_array_literal("{SLE\x1dSJS,RA}") == ["SLE\x1dSJS", "RA"]

    def test_dimension_decoration(self) -> None:
        assert parse_pg_array_literal("[0:1]={a,b}") == ["a", "b"]

    @pytest.mark.parametrize("literal", [
        None, "", "a,b", "{{1,2},{3,4}}", '{"unterminated}', "{a,,b}", '{"a"b}', "{a",
    ])
    def test_rejects_what_it_cannot_read(self, literal: str | None) -> None:
        assert parse_pg_array_literal(literal) is None


class TestEligibility:

    @staticmethod
    def _col(name: str = "c", **kwargs: object) -> ColumnInfo:
        return ColumnInfo(name=name, data_type=kwargs.pop("data_type", "text"), **kwargs)

    @pytest.mark.parametrize("distinct,ratio,expected", [
        (3, 0.001, True),
        (50, 0.5, True),
        (50, 0.51, False),
        (51, 0.2, True),
        (1000, 0.2, True),
        (1000, 0.21, False),
        (1001, 0.01, False),
        (0, 0.0, False),
        (None, 0.1, False),
        (5, None, False),
    ])
    def test_vocabulary_thresholds(self, distinct: float | None, ratio: float | None, expected: bool) -> None:
        assert top_values_eligible(self._col(), distinct, ratio) is expected

    def test_keys_are_never_eligible(self) -> None:
        assert not top_values_eligible(self._col(is_primary_key=True, is_unique=True), 3, 0.01)
        assert not top_values_eligible(self._col(is_unique=True), 3, 0.01)

    def test_member_of_a_composite_key_can_be(self) -> None:
        col = self._col(is_primary_key=True, is_part_of_composite_primary_key=True)
        assert top_values_eligible(col, 3, 0.01)

    @pytest.mark.parametrize("data_type", ["bytea", "ARRAY", "jsonb", "box"])
    def test_binary_and_structured_types_are_not(self, data_type: str) -> None:
        assert not top_values_eligible(self._col(data_type=data_type), 3, 0.01)

    @pytest.mark.parametrize("name", [
        "AdmEmail3", "e-mail", "phone", "Mobile_No", "fax", "ssn", "password", "first_name",
        "LastName", "admfname2", "admlname3", "userdisplayname", "username", "login",
        "street", "mail_address", "ip", "ip_addr", "customer_name", "ContactName", "forename",
        "owner", "assigned_to", "created_by", "author", "birth_date", "birthday", "dob",
    ])
    def test_personal_column_names_are_not(self, name: str) -> None:
        assert looks_personal(name)
        assert not top_values_eligible(self._col(name), 3, 0.01)

    @pytest.mark.parametrize("name", [
        "frequency", "element", "diagnosis", "status", "zip", "ship_mode", "name",
        "team_long_name", "authority_code", "ownership_type",
    ])
    def test_ordinary_names_pass(self, name: str) -> None:
        assert not looks_personal(name)


class TestEstimateDistinctAndRatio:

    def test_exact_counts_win_and_the_ratio_skips_nulls(self) -> None:
        # 42 unique emails in 17,686 mostly-null rows: one value per non-null row.
        exact = {"distinct_count": 42, "null_count": 17644, "cardinality_ratio": 42 / 17686}
        assert estimate_distinct_and_ratio(exact, PgColumnStatistics(n_distinct=3.0), 17686) == (42.0, 1.0)

    def test_positive_n_distinct_is_a_count(self) -> None:
        pg = PgColumnStatistics(n_distinct=18.0, null_frac=0.0)
        assert estimate_distinct_and_ratio(None, pg, 9000) == (18.0, 0.002)

    def test_negative_n_distinct_is_a_fraction_of_all_rows(self) -> None:
        pg = PgColumnStatistics(n_distinct=-0.25, null_frac=0.5)
        distinct, ratio = estimate_distinct_and_ratio(None, pg, 1000)
        assert distinct == 250.0 and ratio == 0.5

    def test_all_null(self) -> None:
        exact = {"distinct_count": 0, "null_count": 10}
        assert estimate_distinct_and_ratio(exact, None, 10) == (0.0, None)

    @pytest.mark.parametrize("pg,rows", [
        (None, 100),
        (PgColumnStatistics(n_distinct=0.0), 100),
        (PgColumnStatistics(n_distinct=5.0), None),
        (PgColumnStatistics(n_distinct=5.0), 0),
    ])
    def test_unknown_without_stats_or_rows(self, pg: PgColumnStatistics | None, rows: int | None) -> None:
        assert estimate_distinct_and_ratio({"distinct_count": None}, pg, rows) == (None, None)


class TestTopValues:

    def test_frequencies_are_shares_of_non_null_rows(self) -> None:
        # pg_stats counts nulls in the denominator; half the rows null doubles each share.
        pg = PgColumnStatistics(null_frac=0.5, most_common_vals=["a", "b"], most_common_freqs=[0.3, 0.2])
        assert top_values_from_pg_stats(pg) == (["a", "b"], [0.6, 0.4])

    @pytest.mark.parametrize("pg", [
        PgColumnStatistics(),
        PgColumnStatistics(most_common_vals=["a"], most_common_freqs=[0.5, 0.5]),
        PgColumnStatistics(null_frac=1.0, most_common_vals=["a"], most_common_freqs=[0.0]),
    ])
    def test_nothing_usable(self, pg: PgColumnStatistics) -> None:
        assert top_values_from_pg_stats(pg) is None

    def test_from_counted_rows(self) -> None:
        rows = [{"value": "Member", "n": 26, "total": 33}, {"value": "Inactive", "n": 3, "total": 33}]
        assert top_values_from_counts(rows) == (["Member", "Inactive"], [0.7879, 0.0909])


class TestScreenTopValues:

    def test_keeps_a_vocabulary(self) -> None:
        values = ["SLE", "SLE, SJS", "School of Applied Sciences, Technology and Education"]
        assert screen_top_values(values, [0.5, 0.3, 0.2]) == (values, [0.5, 0.3, 0.2])

    def test_drops_long_values_instead_of_cutting_them(self) -> None:
        # A cut value matches nothing when used as a literal, and cut values collide.
        long_a, long_b = "x" * (TOP_VALUE_MAX_CHARS + 1), "x" * (TOP_VALUE_MAX_CHARS + 5)
        assert screen_top_values(["a", long_a, "b", long_b], [0.4, 0.3, 0.2, 0.1]) == (["a", "b"], [0.4, 0.2])

    def test_caps_the_count(self) -> None:
        values = [f"v{i}" for i in range(30)]
        top, shares = screen_top_values(values, [0.01] * 30)
        assert top == values[:TOP_VALUES_LIMIT] and len(shares) == TOP_VALUES_LIMIT

    def test_free_text_column_gets_nothing(self) -> None:
        notes = [
            "All active members can vote for new officers at the meeting",
            "Attend school football game as a group and cheer",
            "Officers only",
        ]
        assert screen_top_values(notes, [0.4, 0.3, 0.3]) is None

    @pytest.mark.parametrize("value", ["amos.nugent@gcccharters.org", "+1 555 123 4567", "(555) 123-4567"])
    def test_contact_details_drop_the_column(self, value: str) -> None:
        assert screen_top_values(["a", "b", value], [0.5, 0.3, 0.2]) is None

    def test_dates_are_not_phone_numbers(self) -> None:
        assert screen_top_values(["2026-06-19", "1998-12-31"], [0.5, 0.5]) is not None


class TestNumericText:

    def test_text_holding_numbers(self) -> None:
        col = ColumnInfo(name="a4", data_type="text")
        assert detect_numeric_text(col, ["102609", "-3.5", "1e3", " 7 ", None]) is True

    def test_text_with_words(self) -> None:
        col = ColumnInfo(name="a2", data_type="text")
        assert detect_numeric_text(col, ["102609", "Benesov"]) is False
        assert detect_numeric_text(col, ["NaN", "Infinity"]) is False

    def test_not_applicable(self) -> None:
        assert detect_numeric_text(ColumnInfo(name="n", data_type="integer"), ["1"]) is None
        assert detect_numeric_text(ColumnInfo(name="t", data_type="character varying"), []) is None
        assert detect_numeric_text(ColumnInfo(name="t", data_type="USER-DEFINED", udt_name="citext"), ["1"]) is True


class TestCountableColumns:

    def test_skips_types_without_equality(self) -> None:
        columns = [
            ColumnInfo(name="id", udt_name="int8"),
            ColumnInfo(name="doc", udt_name="json"),
            ColumnInfo(name="docs", udt_name="_json"),
            ColumnInfo(name="body", udt_name="jsonb"),
            ColumnInfo(name="at", udt_name="point"),
        ]
        assert countable_column_names(columns) == ["id", "body"]


class TestFetchColumnStatistics:

    @pytest.mark.asyncio
    async def test_parses_catalog_rows(self) -> None:
        execute = AsyncMock(return_value=[
            {"reltuples": 4500.0, "column_name": "frequency", "null_frac": 0.0, "n_distinct": 3.0,
             "most_common_vals": '{"POPLATEK MESICNE","POPLATEK TYDNE",SLE}',
             "most_common_freqs": [0.9, 0.08, 0.02], "histogram_bounds": None},
            {"reltuples": 4500.0, "column_name": "account_id", "null_frac": 0.0, "n_distinct": -1.0,
             "most_common_vals": None, "most_common_freqs": None, "histogram_bounds": "{1,2,3}"},
        ])

        stats = await _data_source(execute).fetch_column_statistics("public", "account")

        query, params = execute.await_args.args
        assert "pg_stats" in query and "inherited DESC" in query
        assert params == ("public", "account")
        assert stats.reltuples == 4500.0
        assert stats.columns["frequency"].most_common_vals == ["POPLATEK MESICNE", "POPLATEK TYDNE", "SLE"]
        assert stats.columns["account_id"].histogram_bounds == ["1", "2", "3"]

    @pytest.mark.asyncio
    async def test_never_analyzed_table_has_no_columns(self) -> None:
        execute = AsyncMock(return_value=[{"reltuples": -1.0, "column_name": None}])
        stats = await _data_source(execute).fetch_column_statistics("public", "member")
        assert stats.reltuples == -1.0 and stats.columns == {}

    @pytest.mark.asyncio
    async def test_failure_is_none(self) -> None:
        stats = await _data_source(AsyncMock(side_effect=RuntimeError("denied"))).fetch_column_statistics("s", "t")
        assert stats is None


class TestProfileColumns:

    COLUMNS = [
        ColumnInfo(name="id", data_type="bigint", is_primary_key=True, is_unique=True),
        ColumnInfo(name="frequency", data_type="text"),
        ColumnInfo(name="first_name", data_type="text"),
    ]
    FREQUENCY = PgColumnStatistics(
        null_frac=0.0, n_distinct=3.0,
        most_common_vals=["POPLATEK MESICNE", "POPLATEK TYDNE", "POPLATEK PO OBRATU"],
        most_common_freqs=[0.926, 0.0533, 0.0207],
    )

    @pytest.mark.asyncio
    async def test_top_values_from_pg_stats_and_exact_range(self) -> None:
        # pg_stats extremes come from a sample (races.round read 1..20, truly 1..21),
        # so the range is always scanned.
        execute = AsyncMock(return_value=[{"min_0": "1", "max_0": "11382"}])
        statistics = TableColumnStatistics(reltuples=4500.0, columns={
            "id": PgColumnStatistics(n_distinct=-1.0, histogram_bounds=["1", "11000"]),
            "frequency": self.FREQUENCY,
            "first_name": PgColumnStatistics(n_distinct=-0.95, histogram_bounds=["Adele", "Zoe"]),
        })
        ds = _data_source(execute)

        profiles = await ds.profile_columns("public", "account", self.COLUMNS, statistics)

        [query] = _queries(ds)
        assert query == 'SELECT min("id")::text AS min_0, max("id")::text AS max_0 FROM "public"."account"'
        assert profiles["frequency"].top_values == ["POPLATEK MESICNE", "POPLATEK TYDNE", "POPLATEK PO OBRATU"]
        assert profiles["frequency"].top_value_frequencies == [0.926, 0.0533, 0.0207]
        assert profiles["frequency"].numeric_text is False
        assert (profiles["id"].min_value, profiles["id"].max_value) == ("1", "11382")
        assert profiles["id"].top_values is None and profiles["first_name"].top_values is None
        assert all(p.profiled_at for p in profiles.values())

    @pytest.mark.asyncio
    async def test_personal_columns_get_no_range(self) -> None:
        columns = [
            ColumnInfo(name="phone", data_type="bigint"),
            ColumnInfo(name="birth_date", data_type="date"),
            ColumnInfo(name="amount", data_type="numeric"),
        ]
        execute = AsyncMock(return_value=[{"min_0": "5", "max_0": "90"}])
        ds = _data_source(execute)

        profiles = await ds.profile_columns("s", "t", columns, TableColumnStatistics(), row_count=10)

        [query] = _queries(ds)
        assert '"phone"' not in query and '"birth_date"' not in query
        assert (profiles["amount"].min_value, profiles["amount"].max_value) == ("5", "90")
        assert profiles["phone"].min_value is None and profiles["birth_date"].max_value is None

    @pytest.mark.asyncio
    async def test_no_range_when_the_scan_is_too_big_or_fails(self) -> None:
        statistics = TableColumnStatistics(columns={"id": PgColumnStatistics(histogram_bounds=["1", "9"])})
        columns = self.COLUMNS[:1]

        big = _data_source(AsyncMock())
        profiles = await big.profile_columns("s", "t", columns, statistics, row_count=MIN_MAX_SCAN_MAX_CELLS + 1)
        assert _queries(big) == [] and profiles["id"].min_value is None

        unknown = _data_source(AsyncMock())
        profiles = await unknown.profile_columns("s", "t", columns, statistics)
        assert _queries(unknown) == [] and profiles["id"].min_value is None

        failing = _data_source(AsyncMock(side_effect=RuntimeError("statement timeout")))
        profiles = await failing.profile_columns("s", "t", columns, statistics, row_count=10)
        assert profiles["id"].min_value is None and profiles["id"].profiled_at

    @pytest.mark.asyncio
    async def test_table_counted_empty_gets_no_values(self) -> None:
        # Its pg_stats and reltuples still describe the rows that were deleted.
        ds = _data_source(AsyncMock())
        statistics = TableColumnStatistics(reltuples=5000.0, columns={"frequency": self.FREQUENCY})
        exact = {"frequency": {"distinct_count": 0, "null_count": 0, "cardinality_ratio": None}}

        profiles = await ds.profile_columns("s", "t", self.COLUMNS, statistics, row_count=0, exact_stats=exact)

        assert _queries(ds) == []
        assert profiles["frequency"] == ColumnProfile(profiled_at=profiles["frequency"].profiled_at)
        assert profiles["frequency"].profiled_at

    @pytest.mark.asyncio
    async def test_counts_values_when_pg_stats_has_none(self) -> None:
        # A table never analyzed: the exact counts say which columns qualify,
        # and only those are counted, leaving out values seen once.
        async def execute(query: str, params: object = None, *, local_settings: object = None) -> list[dict]:
            assert local_settings is not None and "lock_timeout" in local_settings
            if query.startswith("SELECT min("):
                return [{"min_0": "1", "max_0": "33"}]
            assert '"frequency"::text' in query and 'FROM "public"."member"' in query
            assert "WHERE n > 1" in query
            return [{"value": "Member", "n": 26, "total": 33}, {"value": "Inactive", "n": 5, "total": 33}]

        ds = _data_source(execute)
        exact = {
            "frequency": {"distinct_count": 4, "null_count": 0, "cardinality_ratio": 4 / 33},
            "first_name": {"distinct_count": 33, "null_count": 0, "cardinality_ratio": 1.0},
        }

        profiles = await ds.profile_columns(
            "public", "member", self.COLUMNS, TableColumnStatistics(reltuples=-1.0),
            row_count=33, exact_stats=exact,
        )

        assert sum("::text AS value" in q for q in _queries(ds)) == 1
        assert profiles["frequency"].top_values == ["Member", "Inactive"]
        assert profiles["frequency"].top_value_frequencies == [0.7879, 0.1515]
        assert profiles["frequency"].numeric_text is False
        assert profiles["first_name"].top_values is None

    @pytest.mark.asyncio
    async def test_counting_is_capped_per_table(self) -> None:
        columns = [ColumnInfo(name=f"c{i}", data_type="text") for i in range(TOP_VALUES_MAX_SCANS_PER_TABLE + 5)]
        exact = {c.name: {"distinct_count": 2, "null_count": 0} for c in columns}
        ds = _data_source(AsyncMock(return_value=[{"value": "a", "n": 50, "total": 100}]))

        profiles = await ds.profile_columns("s", "t", columns, TableColumnStatistics(), row_count=100, exact_stats=exact)

        assert len(_queries(ds)) == TOP_VALUES_MAX_SCANS_PER_TABLE
        assert sum(p.top_values is not None for p in profiles.values()) == TOP_VALUES_MAX_SCANS_PER_TABLE

    @pytest.mark.asyncio
    async def test_no_count_scan_on_a_large_table(self) -> None:
        ds = _data_source(AsyncMock())
        exact = {"frequency": {"distinct_count": 2, "null_count": 0}}

        profiles = await ds.profile_columns(
            "public", "big", self.COLUMNS[1:2], TableColumnStatistics(),
            row_count=TOP_VALUES_SCAN_MAX_ROWS + 1, exact_stats=exact,
        )

        assert _queries(ds) == []
        assert profiles["frequency"].top_values is None

    @pytest.mark.asyncio
    async def test_personal_values_never_stored(self) -> None:
        # schools.admemail3: 42 distinct among 17,686 rows, all but 42 null.
        columns = [ColumnInfo(name="contact", data_type="text")]
        statistics = TableColumnStatistics(reltuples=17686.0, columns={
            "contact": PgColumnStatistics(
                null_frac=0.9, n_distinct=20.0,
                most_common_vals=["amos.nugent@gcccharters.org", "x@y.org"], most_common_freqs=[0.05, 0.05],
            ),
        })

        profiles = await _data_source(AsyncMock()).profile_columns("s", "schools", columns, statistics)

        assert profiles["contact"].top_values is None

    @pytest.mark.asyncio
    async def test_failed_count_keeps_the_rest_of_the_profile(self) -> None:
        execute = AsyncMock(side_effect=RuntimeError("canceling statement due to statement timeout"))
        columns = [ColumnInfo(name="code", data_type="text")]
        statistics = TableColumnStatistics(reltuples=100.0, columns={
            "code": PgColumnStatistics(n_distinct=4.0, null_frac=0.0, histogram_bounds=["1", "2", "3", "4"]),
        })

        profiles = await _data_source(execute).profile_columns("s", "t", columns, statistics)

        execute.assert_awaited_once()
        assert profiles["code"].top_values is None
        assert profiles["code"].numeric_text is True
        assert profiles["code"].profiled_at is not None

    @pytest.mark.asyncio
    async def test_a_failing_column_does_not_stop_the_others(self) -> None:
        statistics = TableColumnStatistics(reltuples=10.0, columns={
            "frequency": PgColumnStatistics(n_distinct=2.0, null_frac=0.0, most_common_vals=["a", "b"],
                                            most_common_freqs=[0.5, 0.5]),
            "id": PgColumnStatistics(n_distinct=2.0),
        })
        columns = [ColumnInfo(name="id", data_type="text"), self.COLUMNS[1]]
        # Bypasses validation to stand in for a stats row the code can't handle.
        statistics.columns["id"].__dict__["null_frac"] = "not a number"

        profiles = await _data_source(AsyncMock()).profile_columns("s", "t", columns, statistics)

        assert "id" not in profiles
        assert profiles["frequency"].top_values == ["a", "b"]


class TestLongIdentifiersKeepTheirStats:
    def test_stats_parse_by_position_not_by_truncated_name(self) -> None:
        from app.sources.external.postgres.postgres_ import (
            build_table_column_stats_sql,
            parse_table_column_stats_row,
        )

        long_a = "share_of_customers_who_returned_within_thirty_days_of_first_purchase"
        long_b = "share_of_customers_who_returned_within_thirty_days_of_first_purchase_eu"
        sql = build_table_column_stats_sql("public", "t", [long_a, long_b])
        assert " AS d0" in sql and " AS d1" in sql

        total, stats = parse_table_column_stats_row(
            {"total_rows": 10, "d0": 4, "n0": 1, "d1": 7, "n1": 0}, [long_a, long_b],
        )
        assert total == 10
        assert [(s["name"], s["distinct_count"], s["null_count"]) for s in stats] == [
            (long_a, 4, 1), (long_b, 7, 0),
        ]


class TestScreensAcrossLocales:
    @pytest.mark.parametrize("name", ["passport_no", "national_id", "iban", "card_number", "salary", "tax_id"])
    def test_identity_and_pay_columns_are_personal(self, name: str) -> None:
        assert looks_personal(name)

    @pytest.mark.parametrize("data_type", ["inet", "cidr", "macaddr"])
    def test_network_addresses_have_no_top_values(self, data_type: str) -> None:
        column = ColumnInfo(name="client", data_type=data_type)
        assert not top_values_eligible(column, distinct_count=5, non_null_ratio=1.0)


@pytest.mark.parametrize(("values", "expected"), [(["02134", "10001"], False), (["0", "12", "3.5"], True)])
def test_zero_padded_codes_are_not_numbers(values: list[str], expected: bool) -> None:
    assert detect_numeric_text(ColumnInfo(name="zip", data_type="text"), values) is expected
