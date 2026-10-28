import pytest

from app.connectors.core.sql.profiling import (
    TOP_VALUE_MAX_CHARS,
    TOP_VALUES_LIMIT,
    ColumnProfile,
    detect_numeric_text,
    distinct_and_ratio_from_counts,
    frequency,
    looks_like_contact_value,
    looks_personal,
    parse_table_column_stats_row,
    range_eligible,
    screen_top_values,
    set_top_values,
    top_values_from_counts,
    vocabulary_eligible,
)


class TestLooksPersonal:
    @pytest.mark.parametrize(
        "name",
        ["email", "E-Mail", "phone_number", "FIRST_NAME", "admfname2", "dob", "date_of_birth",
         "salary", "ip", "client_ip", "owner", "created_by", "street_address", "iban"],
    )
    def test_personal(self, name) -> None:
        assert looks_personal(name)

    @pytest.mark.parametrize("name", ["status", "region", "order_id", "ipo_date", "shipping_mode", "amount"])
    def test_not_personal(self, name) -> None:
        assert not looks_personal(name)


class TestContactValue:
    @pytest.mark.parametrize("value", ["a@b.co", "+44 20 7946 0958", "(555) 123-4567"])
    def test_contact(self, value) -> None:
        assert looks_like_contact_value(value)

    @pytest.mark.parametrize("value", ["2024-01-01", "12345", "OPEN"])
    def test_not_contact(self, value) -> None:
        assert not looks_like_contact_value(value)


class TestScreenTopValues:
    def test_drops_long_values_and_caps_the_count(self) -> None:
        values = ["x" * (TOP_VALUE_MAX_CHARS + 1)] + [f"v{i}" for i in range(30)]
        shares = [0.1] * len(values)
        kept, kept_shares = screen_top_values(values, shares)
        assert len(kept) == TOP_VALUES_LIMIT and len(kept_shares) == TOP_VALUES_LIMIT
        assert kept[0] == "v0"

    def test_mostly_wordy_values_are_free_text(self) -> None:
        assert screen_top_values(["a long sentence with many words in it"] * 3 + ["ok"], [0.25] * 4) is None

    def test_any_contact_value_drops_the_column(self) -> None:
        assert screen_top_values(["OPEN", "a@b.co"], [0.5, 0.5]) is None

    def test_nothing_left(self) -> None:
        assert screen_top_values(["x" * (TOP_VALUE_MAX_CHARS + 1)], [1.0]) is None

    def test_set_top_values_writes_only_screened_values(self) -> None:
        profile = ColumnProfile()
        set_top_values(profile, (["OPEN", "a@b.co"], [0.5, 0.5]))
        assert profile.top_values is None
        set_top_values(profile, (["OPEN", "CLOSED"], [0.6, 0.4]))
        assert profile.top_values == ["OPEN", "CLOSED"]


class TestTopValuesFromCounts:
    def test_shares_are_rounded_and_clamped(self) -> None:
        rows = [
            {"value": "A", "n": 2, "total": 3},
            {"value": "B", "n": 5, "total": 3},
            {"value": None, "n": 1, "total": 3},
            {"value": "C", "n": 0, "total": 3},
        ]
        assert top_values_from_counts(rows) == (["A", "B"], [0.6667, 1.0])

    def test_nothing_left(self) -> None:
        assert top_values_from_counts([{"value": None, "n": 1, "total": 1}]) is None

    def test_frequency(self) -> None:
        assert (frequency(-1), frequency(0.123456), frequency(2)) == (0.0, 0.1235, 1.0)


def eligible(**overrides) -> bool:
    args = {
        "name": "status", "is_unique": False, "is_primary_key": False, "is_part_of_composite_primary_key": False,
        "excluded_type": False, "distinct_count": 5, "non_null_ratio": 0.01,
    }
    args.update(overrides)
    return vocabulary_eligible(**args)


class TestVocabularyEligible:
    def test_a_small_repeated_vocabulary(self) -> None:
        assert eligible()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"is_unique": True},
            {"is_primary_key": True},
            {"excluded_type": True},
            {"name": "customer_email"},
            {"distinct_count": None},
            {"non_null_ratio": None},
            {"distinct_count": 0},
        ],
    )
    def test_never(self, overrides) -> None:
        assert not eligible(**overrides)

    def test_a_composite_key_member_can_be(self) -> None:
        assert eligible(is_primary_key=True, is_part_of_composite_primary_key=True)

    @pytest.mark.parametrize(
        ("distinct", "ratio", "expected"),
        [
            (50, 0.5, True),
            (51, 0.5, False),
            (51, 0.2, True),
            (1000, 0.2, True),
            (1001, 0.2, False),
            (50, 0.51, False),
        ],
    )
    def test_thresholds(self, distinct, ratio, expected) -> None:
        assert eligible(distinct_count=distinct, non_null_ratio=ratio) is expected


class TestRangeEligible:
    @pytest.mark.parametrize(("kind", "name", "expected"), [
        ("numeric", "amount", True),
        ("temporal", "created_at", True),
        ("text", "status", False),
        (None, "payload", False),
        ("temporal", "date_of_birth", False),
        ("numeric", "phone", False),
    ])
    def test_range_eligible(self, kind, name, expected) -> None:
        assert range_eligible(kind, name) is expected


class TestDetectNumericText:
    @pytest.mark.parametrize("kind", ["numeric", "temporal", None])
    def test_only_text_columns(self, kind) -> None:
        assert detect_numeric_text(kind, ["1"]) is None

    @pytest.mark.parametrize("values", [["1", "-0.5", "1e5", ".5", " 7 "], ["0"], ["0.5"]])
    def test_all_numbers(self, values) -> None:
        assert detect_numeric_text("text", values) is True

    def test_a_leading_zero_is_an_identifier(self) -> None:
        assert detect_numeric_text("text", ["1", "007"]) is False

    def test_any_word_is_not_numeric(self) -> None:
        assert detect_numeric_text("text", ["1", "two"]) is False

    def test_no_values(self) -> None:
        assert detect_numeric_text("text", [None]) is None


class TestDistinctAndRatioFromCounts:
    def test_ratio_is_over_non_null_rows(self) -> None:
        assert distinct_and_ratio_from_counts(5, 50, 100) == (5.0, 0.1)

    def test_all_null(self) -> None:
        assert distinct_and_ratio_from_counts(0, 100, 100) == (0.0, None)

    @pytest.mark.parametrize(("distinct", "nulls", "rows"), [(5, 0, None), (5, 0, 0), (None, 0, 10), (5, None, 10)])
    def test_unknown(self, distinct, nulls, rows) -> None:
        assert distinct_and_ratio_from_counts(distinct, nulls, rows) == (None, None)


class TestParseTableColumnStatsRow:
    def test_positional_aliases_in_either_case(self) -> None:
        total, stats = parse_table_column_stats_row(
            {"TOTAL_ROWS": "10", "D0": "4", "N0": "1", "d2": 10, "n2": 0}, ["status", "", "id"],
        )
        assert total == 10
        assert stats == [
            {"name": "status", "ordinal_position": 0, "distinct_count": 4, "null_count": 1, "cardinality_ratio": 0.4},
            {"name": "id", "ordinal_position": 2, "distinct_count": 10, "null_count": 0, "cardinality_ratio": 1.0},
        ]

    def test_unreadable_counts_are_none(self) -> None:
        total, (stats,) = parse_table_column_stats_row({"total_rows": 0, "d0": "x"}, ["c"])
        assert total == 0
        assert (stats["distinct_count"], stats["null_count"], stats["cardinality_ratio"]) == (None, None, None)

    def test_unreadable_total_uses_the_fallback(self) -> None:
        total, _ = parse_table_column_stats_row({"total_rows": "lots"}, [], fallback_total_rows=7)
        assert total == 7
