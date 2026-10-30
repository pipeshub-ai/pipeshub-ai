"""InnoDB table names and ids."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.mariadb.innodb import decode_innodb_name
from app.sources.external.mariadb.mariadb_ import MariaDBDataSource


class TestDecode:
    @pytest.mark.parametrize(("name", "expected"), [
        ("shop/orders", ("shop", "orders")),
        ("shop/my@002ditems", ("shop", "my-items")),
        ("shop/with@0020space", ("shop", "with space")),
        ("shop/x@0040y", ("shop", "x@y")),
        ("shop/d@00241", ("shop", "d$1")),
        ("shop/a@002eb", ("shop", "a.b")),
        ("shop/@8868", ("shop", "表")),
        ("my@002ddb/t", ("my-db", "t")),
    ])
    def test_four_hex_digit_codes(self, name, expected) -> None:
        assert decode_innodb_name(name) == expected

    @pytest.mark.parametrize("name", [
        "shop/@1On@0v",       # short codes for Latin letters: not decoded
        "shop/t#P#p0",        # a partition
        "shop/#sql-1a2b_3",   # an internal table
        "SYS_TABLES",         # InnoDB's own, no database
        "shop/",
        "shop/a@00",          # cut short
    ])
    def test_what_is_not_a_plain_table_gets_no_name(self, name) -> None:
        assert decode_innodb_name(name) is None



class TestReadTableIds:
    @staticmethod
    def source(rows, database="shop") -> tuple:
        client = MagicMock()
        client.database = database
        client.execute_query = AsyncMock(return_value=rows)
        return MariaDBDataSource(client), client

    async def test_ids_of_this_database_by_table_name(self) -> None:
        source, client = self.source([
            {"name": "shop/orders", "table_id": 18},
            {"name": "shop/my@002ditems", "table_id": 19},
            {"name": "shop2/orders", "table_id": 40},
            {"name": "shop/t#P#p0", "table_id": 41},
            {"name": "shop/@1On@0v", "table_id": 42},
        ])
        assert await source.read_table_ids("shop") == {"orders": 18, "my-items": 19}
        assert "INNODB_SYS_TABLES" in client.execute_query.await_args.args[0]

    async def test_an_encoded_database_name_is_matched_decoded(self) -> None:
        source, _ = self.source([{"name": "my@002ddb/t", "table_id": 7}, {"name": "other/t", "table_id": 8}])
        assert await source.read_table_ids("my-db") == {"t": 7}

    async def test_two_names_that_decode_the_same_get_no_id(self) -> None:
        source, _ = self.source([{"name": "shop/a@002db", "table_id": 7}, {"name": "shop/a@002Db", "table_id": 8},
                                 {"name": "shop/c", "table_id": 9}])
        assert await source.read_table_ids("shop") == {"c": 9}

    async def test_a_refused_read_raises(self) -> None:
        source, client = self.source([])
        client.execute_query = AsyncMock(side_effect=RuntimeError("1227 Access denied; you need PROCESS"))
        with pytest.raises(RuntimeError):
            await source.read_table_ids("shop")
