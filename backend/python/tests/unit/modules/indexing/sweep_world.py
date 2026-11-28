"""A world for the property projection sweep tests: the projection tests' in-memory graph, plus apps documents
with a compare-and-set, the two selections (the reference of the engines' statements), an index state, and
controllable leases, clock and stored values."""

import copy
import logging
from typing import Any

from app.core.org_context import current_org_id
from app.models.record_metadata import (
    MetadataProvenance,
    MetadataValue,
    PropertyType,
    RecordMetadata,
)
from app.modules.indexing.property_projection_sweep import (
    GraphStateStore,
    PropertiesUnavailable,
    PropertyProjectionSweep,
)
from app.modules.record_metadata.projection import ScopeUnionCache, doc_stamp
from app.services.graph_db.conditions import check_conditions, holds
from tests.unit.modules.record_metadata.projection_world import (
    ORG,
    FakeGraph,
    defs,
    registry,
)

KB = "kb-1"
NOW = 10_000_000


def meta(**values: object) -> RecordMetadata:
    return RecordMetadata(schema_id=f"{ORG}:{KB}:1", values={
        name: MetadataValue(value=value, provenance=MetadataProvenance.PROVIDED, source="push")  # type: ignore[arg-type]
        for name, value in values.items()
    })


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def stale(doc: dict[str, Any], props_hash: str, props_gen: int) -> bool:
    """The selection predicate, as the engines' statements state it."""
    stamp = doc_stamp(doc)
    if stamp <= 0:
        return False
    return (
        doc.get("propsSet") != props_hash
        or not _number(doc.get("propsGen")) or doc["propsGen"] < props_gen
        or not _number(doc.get("propsSrc")) or doc["propsSrc"] < stamp
    )


class SweepGraph(FakeGraph):
    def __init__(self, style: str = "arango") -> None:
        super().__init__(style)
        self.apps: dict[str, dict[str, Any]] = {}
        self.index = "online"
        self.ensure_calls = 0
        self.seeks: list[dict[str, Any]] = []
        self.walks: list[dict[str, Any]] = []
        self.selected_rows = 0
        self.orgs_seen: set[str] = set()
        self.lists_every_org = False

    # The stores that keep each org apart (PostgreSQL) fail a read made outside an org; so does this one, and a read made in one org
    # lists that org's apps only. ``orgs_seen`` is what the reads were made in.
    def _in_org(self) -> str:
        org = current_org_id()
        self.orgs_seen.add(org)
        return org

    async def get_document(self, key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False):  # noqa: ANN201
        self._in_org()
        if collection == "apps":
            return copy.deepcopy(self.apps.get(key))
        return await super().get_document(key, collection, transaction, raise_on_error=raise_on_error)

    async def get_all_documents(self, collection: str, transaction: str | None = None) -> list[dict]:
        assert collection == "apps"
        org = self._in_org()
        # ArangoDB and Neo4j list the apps of every org in one read; PostgreSQL only the bound org's
        return copy.deepcopy([a for a in self.apps.values() if self.lists_every_org or a.get("orgId") == org])

    async def update_node_fields_if(self, key: str, collection: str, updates: dict, conditions: list, transaction: str | None = None) -> bool:
        self._in_org()
        check_conditions(conditions)
        doc = self.apps[key]
        if not all(holds(c, doc) for c in conditions):
            return False
        doc.update(updates)
        return True

    async def ensure_record_change_index(self) -> None:
        self.ensure_calls += 1

    async def record_change_index_online(self) -> bool:
        return self.index == "online"

    def _live(self, connector_id: str, org_id: str) -> list[dict[str, Any]]:
        return [d for d in self.records.values()
                if d.get("connectorId") == connector_id and d.get("orgId") == org_id and not d.get("isDeleted")]

    async def get_records_updated_since(self, connector_id: str, org_id: str, *, since: int, after_id: str, props_hash: str,
                                        props_gen: int, limit: int, transaction: str | None = None) -> list[dict]:
        assert self.index == "online", "queried a change index that is not online"
        assert self._in_org() == org_id, "a read for one org made in another"
        self.seeks.append({"since": since, "after_id": after_id})
        rows = [d for d in self._live(connector_id, org_id) if _number(d.get("updatedAtTimestamp"))
                and (d["updatedAtTimestamp"] > since or (d["updatedAtTimestamp"] == since and d["_key"] > after_id))
                and stale(d, props_hash, props_gen)]
        rows.sort(key=lambda d: (d["updatedAtTimestamp"], d["_key"]))
        self.selected_rows += len(rows[:limit])
        return copy.deepcopy(rows[:limit])

    async def get_records_needing_projection(self, connector_id: str, org_id: str, *, after_key: str | None, props_hash: str,
                                             props_gen: int, limit: int, transaction: str | None = None) -> list[dict]:
        assert self._in_org() == org_id, "a read for one org made in another"
        self.walks.append({"after_key": after_key})
        rows = [d for d in self._live(connector_id, org_id) if (not after_key or d["_key"] > after_key)
                and stale(d, props_hash, props_gen)]
        rows.sort(key=lambda d: d["_key"])
        self.selected_rows += len(rows[:limit])
        return copy.deepcopy(rows[:limit])


class FakeLease:
    def __init__(self, leases: "FakeLeases", key: str) -> None:
        self._leases, self._key, self.lost = leases, key, False

    def held(self) -> bool:
        return not self.lost

    async def release(self) -> None:
        self._leases.holders.pop(self._key, None)


class FakeLeases:
    """One shared by every pod of a test: a key is held by one lease at a time."""

    def __init__(self) -> None:
        self.holders: dict[str, FakeLease] = {}
        self.busy: set[str] = set()

    async def acquire(self, key: str) -> FakeLease | None:
        if key in self.holders:
            return None
        self.holders[key] = lease = FakeLease(self, key)
        return lease

    async def record_busy(self, record_id: str) -> bool:
        return record_id in self.busy


class NoLeases:
    """A deployment without a lease manager's guarantees: everyone gets a lease."""

    async def acquire(self, key: str) -> FakeLease:
        return FakeLease(FakeLeases(), key)

    async def record_busy(self, record_id: str) -> bool:
        return False


class Values:
    """Stored values by record id; an id in ``unreadable`` cannot be read."""

    def __init__(self, default: RecordMetadata | None) -> None:
        self.default = default
        self.by_id: dict[str, RecordMetadata | None] = {}
        self.unreadable: set[str] = set()
        self.reads: list[str] = []

    async def __call__(self, doc: dict[str, Any]) -> RecordMetadata | None:
        key = doc["_key"]
        self.reads.append(key)
        if key in self.unreadable:
            raise PropertiesUnavailable("no_pointer")
        return self.by_id.get(key, self.default)


class Clock:
    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


class World:
    """A knowledge base ``kb-1`` whose schema projects ``team`` (a string)."""

    def __init__(self, style: str = "arango") -> None:
        self.graph = SweepGraph(style)
        self.graph.apps[KB] = {"_key": KB, "orgId": ORG, "type": "KB"}
        self.registry, self.config = registry()
        self.values = Values(meta(team="a"))
        self.clock = Clock()
        self.leases: Any = FakeLeases()
        self.sleeps: list[float] = []

    async def register(self, *names: str) -> None:
        await self.registry.register(ORG, KB, defs(*[(n, PropertyType.STRING) for n in names]))

    def add(self, key: str, *, updated: int | None = None, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
        from tests.unit.modules.record_metadata.projection_world import kb_doc

        doc = kb_doc(key, kb_id=KB, updated=updated if updated is not None else self.clock.now, **fields)
        self.graph.records[key] = doc
        return doc

    async def org_ids(self) -> list[str]:
        return sorted({a["orgId"] for a in self.graph.apps.values()})

    def sweep(self, *, cache: ScopeUnionCache | None = None, leases: Any = None, **over: Any) -> PropertyProjectionSweep:  # noqa: ANN401
        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        params: dict[str, Any] = {
            "graph": self.graph, "registry": self.registry, "state": GraphStateStore(self.graph),
            "leases": leases or self.leases, "reader": self.values, "cache": cache or ScopeUnionCache(),
            "log": logging.getLogger("sweep-test"), "clock": self.clock, "sleep": sleep, "enabled": lambda: True,
            "page_size": 50, "interval_ms": 900_000, "org_ids": self.org_ids,
        }
        return PropertyProjectionSweep(**(params | over))

    def state(self) -> dict[str, Any]:
        import json

        return json.loads(self.graph.apps[KB]["propSweep"])

    def projected(self) -> int:
        return sum(1 for d in self.graph.records.values() if d.get("propsSet") is not None)
