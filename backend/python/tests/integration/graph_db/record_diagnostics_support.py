"""One driver over the three graph stores for the record processing diagnostics tests.

``Store`` gives a test the production provider of a store plus the few raw operations a test needs and the
provider does not offer (a write that bypasses schema validation, as an older process or a plain field reset
would make). ArangoDB and Neo4j come from ``tests.integration.real_graph``; PostgreSQL from the fixtures of
``tests/integration/graph_db/postgres``.
"""

from __future__ import annotations

import contextlib
import copy
import uuid
from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgContext, bind

RECORDS = CollectionNames.RECORDS.value


def base_record(key: str, org: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "id": key, "orgId": org, "recordName": key, "externalRecordId": key, "recordType": "FILE",
        "origin": "CONNECTOR", "connectorName": "KB", "connectorId": f"{org}-c", "version": 1,
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "indexingStatus": "NOT_STARTED", **extra,
    }


class _OrgBound:
    """Every call of a PostgreSQL provider runs in the ambient org, as a request would."""

    def __init__(self, provider: Any, org: str) -> None:  # noqa: ANN401
        self._p, self._org = provider, org

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        attr = getattr(self._p, name)
        if not callable(attr):
            return attr

        async def call(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            with bind(OrgContext(self._org, None, "job", "job:test", "t")):
                return await attr(*a, **k)

        return call


class Store:
    """``name`` is ``arango``, ``neo4j`` or ``postgres``; ``p`` is the provider, bound to ``org`` for PostgreSQL."""

    def __init__(self, name: str, provider: Any, org: str, raw: Any = None) -> None:  # noqa: ANN401
        self.name = name
        self.raw_provider = provider
        self.p = _OrgBound(provider, org) if name == "postgres" else provider
        self.org = org
        self._pg_db = raw
        self.keys: list[str] = []

    def key(self, label: str = "r") -> str:
        key = f"{self.org}-{label}-{len(self.keys)}"
        self.keys.append(key)
        return key

    async def put(self, key: str, **fields: Any) -> None:  # noqa: ANN401
        """A record written through the provider (schema validated)."""
        doc = base_record(key, self.org, **fields)
        if self.name == "postgres":
            doc = {"_key": key, **{k: v for k, v in doc.items() if k != "id"}}
        assert await self.p.batch_upsert_nodes([doc], RECORDS)

    async def doc(self, key: str) -> dict[str, Any]:
        found = await self.p.get_document(key, RECORDS)
        assert found is not None, key
        return found

    async def raw_set(self, key: str, fields: dict[str, Any]) -> None:
        """A write that skips the provider's validation and says what it means by ``None``: the field is removed."""
        if self.name == "arango":
            await self.raw_provider.http_client.execute_aql(
                "FOR d IN @@c FILTER d._key == @k UPDATE d WITH @f IN @@c OPTIONS { keepNull: false }",
                {"@c": RECORDS, "k": key, "f": fields},
            )
        elif self.name == "neo4j":
            await self.raw_provider.client.execute_query(
                "MATCH (n:Record {id: $k}) SET n += $f", parameters={"k": key, "f": fields},
            )
        else:
            import json

            with bind(OrgContext(self.org, None, "job", "job:test", "t")):
                await self._pg_db.run(lambda tx: tx.execute(
                    "UPDATE graph.nodes SET doc = (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) "
                    "FROM jsonb_each(doc || $3::jsonb) e WHERE e.value <> 'null'::jsonb) "
                    "WHERE org_id = $1 AND coll = 'records' AND id = $2",
                    self.org, key, json.dumps(fields)), write=True)

    async def cleanup(self) -> None:
        for key in self.keys:
            with contextlib.suppress(Exception):
                await self.p.delete_nodes([key], RECORDS)


def run_fields(doc: dict[str, Any]) -> dict[str, Any]:
    """The run diagnostics of a stored record, absent and null alike left out, floats that are whole as ints."""
    out: dict[str, Any] = {}
    for name in ("firstQueuedAt", "indexingAttempts", "failedAt", "pageCount"):
        value = doc.get(name)
        if value is None:
            continue
        out[name] = int(value) if isinstance(value, float) and value.is_integer() else value
    return out


def new_org() -> str:
    return uuid.uuid4().hex[:24]  # 24 hex digits: the PostgreSQL graph refuses any other org id


def use_pre_diagnostics_validator(monkeypatch: Any) -> None:  # noqa: ANN401
    """The validator of a process older than the release that added the fields: its record schema does not list them."""
    import copy

    from app.schema import node_validator

    real = node_validator.get_node_schema
    old_schemas: dict[str, Any] = {}

    def lookup(collection: str) -> Any:  # noqa: ANN401
        if collection != RECORDS:
            return real(collection)
        if collection not in old_schemas:
            old = copy.deepcopy(real(collection))
            for name in ("firstQueuedAt", "failedAt", "indexingAttempts", "pageCount"):
                old["properties"].pop(name, None)
            old_schemas[collection] = old
        return old_schemas[collection]

    monkeypatch.setattr(node_validator, "get_node_schema", lookup)


DIAGNOSTIC_NAMES = ("firstQueuedAt", "failedAt", "indexingAttempts", "pageCount")


def _record_schema() -> dict[str, Any]:
    from app.schema.arango.documents import record_schema

    return record_schema


def narrow_schema(*, drop_status: str | None = None) -> dict[str, Any]:
    """The record schema of an image older than the fields: no four properties, no generation marker."""
    old = copy.deepcopy(_record_schema())
    for name in DIAGNOSTIC_NAMES:
        old["rule"]["properties"].pop(name, None)
    old["rule"].pop("$comment", None)
    if drop_status:
        enum = old["rule"]["properties"]["indexingStatus"]["enum"]
        old["rule"]["properties"]["indexingStatus"]["enum"] = [v for v in enum if v != drop_status]
    return old




async def drop_database(provider: Any) -> None:  # noqa: ANN401
    """Drop the scratch database an ArangoDB test made for itself (each holds a full set of collections)."""
    client = provider.http_client
    session = await client._get_session()
    async with session.delete(f"{client.base_url}/_api/database/{client.database}") as response:
        await response.read()
