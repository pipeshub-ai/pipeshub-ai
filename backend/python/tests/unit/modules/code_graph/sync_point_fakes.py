"""In-memory sync points for build-state tests."""
from app.config.constants.arangodb import CollectionNames


class FakeSyncPoints:
    """Sync points held in memory, with the provider's read, upsert and
    compare-and-set semantics (None matches a missing field)."""

    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        # Keys of records that exist, for the reads that ask whether one is gone.
        self.records: set[str] = set()
        # Called after a settle reads state and before it writes: lets a test
        # land a request in exactly that window.
        self.before_cas = None

    async def get_nodes_by_filters(self, collection, filters, return_fields=None,
                                   transaction=None, *, raise_on_error=False) -> list[dict]:
        assert collection == CollectionNames.SYNC_POINTS.value
        return [dict(p) for p in self.points.values()
                if all(p.get(f) == v for f, v in filters.items())]

    async def upsert_sync_point(self, sync_point_key, sync_point_data, collection,
                                transaction=None) -> bool:
        self.points.setdefault(sync_point_key, {"syncPointKey": sync_point_key}).update(
            sync_point_data
        )
        return True

    async def get_nodes_by_field_in(self, collection, field_name, field_values,
                                    return_fields=None, transaction=None, *,
                                    raise_on_error=False) -> list[dict]:
        assert (collection, field_name) == (CollectionNames.RECORDS.value, "_key")
        return [{"_key": key} for key in field_values if key in self.records]

    async def remove_sync_point(self, key, collection, transaction=None) -> None:
        self.points.pop(key, None)

    async def update_sync_point_if(self, sync_point_key, expected, sync_point_data,
                                   collection) -> bool:
        if self.before_cas is not None:
            hook, self.before_cas = self.before_cas, None
            await hook()
        point = self.points.get(sync_point_key)
        if point is None or any(point.get(f) != v for f, v in expected.items()):
            return False
        point.update(sync_point_data)
        return True
