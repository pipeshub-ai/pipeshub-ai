"""MariaDB column facts, row values as JSON, and sample order."""

import datetime
import json

from app.connectors.sources.mariadb.columns import (
    ColumnFacts,
    column_fqn,
    decode_rows,
    json_default,
    sample_order,
)
from app.sources.external.mariadb.mariadb_ import ColumnInfo


def col(name, **extra) -> ColumnInfo:
    return ColumnInfo(name=name, data_type=extra.pop("data_type", "int"), **extra)


class TestFacts:
    def test_column_type_and_no_textual_null_default(self) -> None:
        facts = ColumnFacts.of(col("note", data_type="varchar", column_type="varchar(64)", nullable=True,
                                   default="NULL"))
        assert (facts.data_type, facts.default, facts.nullable) == ("varchar(64)", None, True)
        assert ColumnFacts.of(col("n", default="'NULL'")).default == "'NULL'"

    def test_ids(self) -> None:
        assert column_fqn('shop."a.b"', "x.y") == 'shop."a.b".x.y'


class TestJson:
    def test_values_json_cannot_write(self) -> None:
        assert json_default(b"\x00\xff") == "0x00ff"
        assert json_default(datetime.timedelta(hours=-10)) == "-10:00:00"
        assert json_default(datetime.timedelta(hours=838, minutes=59, seconds=59)) == "838:59:59"
        assert json_default(datetime.timedelta(seconds=1, microseconds=5)) == "00:00:01.000005"
        assert json.dumps({"d": datetime.date(2024, 1, 2)}, default=json_default) == '{"d": "2024-01-02"}'

    def test_bits_are_numbers(self) -> None:
        rows = decode_rows([{"flags": b"\x05", "name": b"x"}], [col("flags", data_type="bit"), col("name")])
        assert rows == [{"flags": 5, "name": b"x"}]


class TestSampleOrder:
    def test_primary_key_first(self) -> None:
        assert sample_order([col("a"), col("b")], ["b"], None) == ["b"]

    def test_a_not_null_unique_column_next(self) -> None:
        assert sample_order([col("a"), col("code", is_unique=True, nullable=False)], [], None) == ["code"]

    def test_every_column_while_small_none_when_big(self) -> None:
        columns = [col("a", nullable=True), col("b", is_unique=True, nullable=True)]
        assert sample_order(columns, [], 10) == ["a", "b"]
        assert sample_order(columns, [], 5_000_000) is None
        assert sample_order(columns, [], None) is None
        assert sample_order(columns, [], 0) is None  # InnoDB reports 0 for a table it has not counted
