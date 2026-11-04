"""The saved Snowflake sync state and the pure decisions over it."""

from typing import Never

import pytest

from app.connectors.sources.snowflake.catalog_state import SnowflakeTableState
from app.connectors.sources.snowflake.catalog_sync import (
    Scope,
    Snapshot,
    database_of,
    gone_since,
    next_state,
    unreadable_tables,
)
from app.connectors.sources.snowflake.sync_state import (
    INDEX_KEY,
    SYNC_STATE_VERSION,
    DatabaseState,
    SnowflakeStateStore,
    database_key,
)


class FakeSyncPoint:
    def __init__(self) -> None:
        self.data: dict = {}

    async def read_sync_point(self, key) -> dict:
        return self.data.get(key, {})

    async def update_sync_point(self, key, value) -> None:
        self.data[key] = dict(value)

    async def delete_sync_point(self, key) -> None:
        self.data.pop(key, None)


def table_state(fqn: str) -> SnowflakeTableState:
    database, schema, name = fqn.split(".")
    return SnowflakeTableState(database=database, schema_name=schema, table_name=name, created=f"c-{fqn}")


class TestStore:
    async def test_round_trip_by_database(self) -> None:
        store = SnowflakeStateStore(FakeSyncPoint())
        states = {"DB": DatabaseState(tables={"DB.S.T": table_state("DB.S.T")}, views={"DB.S.V": "t"})}
        await store.save("Acct", states)
        assert await store.load("acct") == states

    async def test_another_account_or_version_is_no_state(self) -> None:
        point = FakeSyncPoint()
        store = SnowflakeStateStore(point)
        await store.save("acct", {"DB": DatabaseState()})
        assert await store.load("other") is None
        point.data[INDEX_KEY]["state_version"] = SYNC_STATE_VERSION + 1
        assert await store.load("acct") is None

    async def test_no_index_is_no_state(self) -> None:
        assert await SnowflakeStateStore(FakeSyncPoint()).load("acct") is None

    @pytest.mark.parametrize("slice_value", [None, {"state": "{not json"}, {"state": '{"tables": 3}'}])
    async def test_a_missing_or_corrupt_slice_is_no_state(self, slice_value) -> None:
        point = FakeSyncPoint()
        store = SnowflakeStateStore(point)
        await store.save("acct", {"A": DatabaseState(), "DB": DatabaseState()})
        if slice_value is None:
            del point.data[database_key("DB")]
        else:
            point.data[database_key("DB")] = slice_value
        assert await store.load("acct") is None

    async def test_a_store_that_cannot_be_read_raises(self) -> None:
        point = FakeSyncPoint()
        store = SnowflakeStateStore(point)
        await store.save("acct", {"DB": DatabaseState()})

        async def refuse(key) -> Never:
            raise ConnectionError("graph store unavailable")

        point.read_sync_point = refuse
        with pytest.raises(ConnectionError):
            await store.load("acct")

    async def test_slices_of_databases_no_longer_kept_are_dropped(self) -> None:
        point = FakeSyncPoint()
        store = SnowflakeStateStore(point)
        old = {"A": DatabaseState(), "B": DatabaseState()}
        await store.save("acct", old)
        await store.save("acct", {"A": DatabaseState()}, previous=old)
        assert database_key("B") not in point.data
        assert point.data[INDEX_KEY]["databases"] == ["A"]


class TestDecisions:
    def test_database_of(self) -> None:
        assert database_of("DB.S.T") == "DB"
        assert database_of('"a.b".S.ST/dir/file.csv') == "a.b"
        assert database_of("DB.S") == "DB"

    def test_unreadable_tables(self) -> None:
        stored = {f: table_state(f) for f in ("DB.S.A", "DB.T.B", "D2.S.C", "DB.S.D")}
        snapshot = Snapshot(unread_tables={"DB.S.D"})
        scope = Scope(databases={"D2"}, schemas={"DB.T"})
        assert unreadable_tables(stored, snapshot, scope) == {"DB.T.B", "D2.S.C", "DB.S.D"}

    def test_gone_since_skips_unreadable_scopes(self) -> None:
        stored = {
            "DB": DatabaseState(
                views={"DB.S.V1": "t", "DB.T.V2": "t"},
                files={"DB.S.ST/a": "m", "DB.S.ST2/b": "m"},
                stages=["DB.S.ST", "DB.S.ST2"],
                schemas=["DB.S", "DB.OLD"],
            ),
            "GONE": DatabaseState(),
        }
        snapshot = Snapshot(databases={"DB"}, schemas={"DB.S"}, stages={"DB.S.ST2"})
        scope = Scope(schemas={"DB.T"}, stages={"DB.S.ST2"})
        gone = gone_since(stored, snapshot, scope)
        assert gone.views == ["DB.S.V1"]
        assert gone.files == ["DB.S.ST/a"]
        assert gone.stages == ["DB.S.ST"]
        assert gone.schemas == ["DB.OLD"]
        assert gone.databases == ["GONE"]

    def test_next_state_groups_by_database_and_keeps_what_it_is_told(self) -> None:
        stored = {"DB": DatabaseState(views={"DB.T.KEPT": "old"}, files={"DB.T.ST/x": "m"})}
        snapshot = Snapshot(
            databases={"DB", "D2"}, schemas={"DB.S", "D2.S"}, views={"DB.S.V": "t"},
            stages={"DB.S.ST"}, files={"DB.S.ST/f": "m"},
        )
        states = next_state(
            snapshot, stored,
            tables={"DB.S.A": table_state("DB.S.A")},
            kept_views={"DB.T.KEPT"}, kept_files={"DB.T.ST/x"}, kept_stages={"DB.T.ST"}, kept_schemas={"DB.T"},
        )
        db = states["DB"]
        assert set(db.tables) == {"DB.S.A"}
        assert db.views == {"DB.S.V": "t", "DB.T.KEPT": "old"}
        assert db.files == {"DB.S.ST/f": "m", "DB.T.ST/x": "m"}
        assert db.stages == ["DB.S.ST", "DB.T.ST"] and db.schemas == ["DB.S", "DB.T"]
        assert states["D2"].schemas == ["D2.S"]
