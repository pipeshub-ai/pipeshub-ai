"""``Lives`` over the real push service on Neo4j and ArangoDB (the ``env`` fixture of ``test_kb_push_real_backends``)."""

from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.models.record_metadata import PropertyDefinition, PropertyType
from tests.integration.graph_db import test_kb_push_real_backends as IT

RECORDS = CollectionNames.RECORDS.value


class PushLives:
    def __init__(self, env: IT.Env) -> None:
        self.env = env
        self.graph: Any = env.graph
        self.org_id = env.org_id
        self.record_id = env.key("rec-1")
        self.schema_id: str | None = None
        IT.record_keys(env, "rec-1")

    async def _schema(self) -> str:
        if self.schema_id is None:
            schema = await self.env.service.schemas.register(
                self.env.org_id,
                self.env.kb_id,
                [
                    PropertyDefinition(
                        name="n", type=PropertyType.INT, display_label="N"
                    )
                ],
            )
            self.schema_id = schema.schema_id
        return self.schema_id

    async def commit(self, revision: int) -> bytes:
        env = self.env
        await env.push(
            env.body(
                version=revision,
                text=f"rev {revision}",
                metadata={"schemaId": await self._schema(), "values": {"n": revision}},
            )
        )
        record = await env.record()
        assert record is not None
        return env.storage.docs[record["externalRecordId"]]

    async def purge(self) -> None:
        await self.graph.delete_nodes_and_edges([self.record_id], RECORDS)  # the providers differ in what they return, so the read below decides
        assert await self.env.record() is None

    async def repush(self) -> None:
        await self.commit(1)
        record = await self.env.record()
        assert record is not None and record.get("isDeleted") is not True
