"""Adapters that let the insert-only cases of ``record_insert_only`` run on ArangoDB, Neo4j and PostgreSQL."""

from __future__ import annotations

import uuid
from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgContext, bind

RECORDS = CollectionNames.RECORDS.value


class _Base:
    name = "?"

    def __init__(self, provider: Any) -> None:  # noqa: ANN401
        self.p = provider

    async def upsert(self, records: list[Any], *, release_trashed: bool = False) -> None:
        await self.p.batch_upsert_records(records, release_trashed_external_ids=release_trashed)

    async def upsert_nodes(self, docs: list[dict[str, Any]]) -> None:
        await self.p.batch_upsert_nodes(docs, RECORDS)

    async def doc(self, key: str) -> dict[str, Any] | None:
        return await self.p.get_document(key, RECORDS)

    async def type_doc(self, key: str, collection: str) -> dict[str, Any] | None:
        return await self.p.get_document(key, collection)

    async def trash(self, key: str, connector_id: str) -> str:
        batch = uuid.uuid4().hex
        result = await self.p.soft_delete_records([key], connector_id, delete_source="CONNECTOR", batch_id=batch)
        assert result["successfully_deleted"] == 1, result
        return batch

    async def restore(self, key: str, connector_id: str, batch_id: str) -> list[str]:
        return await self.p.restore_records([{"id": key}], batch_id, connector_id=connector_id)


class ArangoStore(_Base):
    name = "arangodb"

    async def merge_like_an_older_process(self, key: str, doc: dict[str, Any]) -> None:
        await self.p.http_client.batch_insert_documents(RECORDS, [{"_key": key, **doc}], overwrite=True, overwrite_mode="update")


class Neo4jStore(_Base):
    name = "neo4j"

    async def merge_like_an_older_process(self, key: str, doc: dict[str, Any]) -> None:
        await self.p.client.execute_query("MATCH (n:Record {id: $id}) SET n += $doc", parameters={"id": key, "doc": doc})


class PostgresStore(_Base):
    name = "postgres"

    def __init__(self, provider: Any, org: str) -> None:  # noqa: ANN401
        super().__init__(provider)
        self.org = org

    def _bound(self):  # noqa: ANN202
        return bind(OrgContext(self.org, None, "job", "job:test", "t"))

    async def upsert(self, records: list[Any], *, release_trashed: bool = False) -> None:
        with self._bound():
            await super().upsert(records, release_trashed=release_trashed)

    async def upsert_nodes(self, docs: list[dict[str, Any]]) -> None:
        with self._bound():
            await super().upsert_nodes(docs)

    async def doc(self, key: str) -> dict[str, Any] | None:
        with self._bound():
            return await super().doc(key)

    async def type_doc(self, key: str, collection: str) -> dict[str, Any] | None:
        with self._bound():
            return await super().type_doc(key, collection)

    async def trash(self, key: str, connector_id: str) -> str:
        with self._bound():
            return await super().trash(key, connector_id)

    async def restore(self, key: str, connector_id: str, batch_id: str) -> list[str]:
        with self._bound():
            return await super().restore(key, connector_id, batch_id)

    async def merge_like_an_older_process(self, key: str, doc: dict[str, Any]) -> None:
        import json

        with self._bound():
            await self.p._q(lambda tx: tx.execute(
                "UPDATE graph.nodes SET doc = doc || $3::jsonb WHERE org_id = $1 AND coll = 'records' AND id = $2",
                self.org, key, json.dumps(doc)), None, write=True)
