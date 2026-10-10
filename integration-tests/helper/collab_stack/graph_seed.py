"""Writes to, and reads from, the lane's graph through the product's own ``IGraphDBProvider``.

Runs under the services' Python (the repo venv), not the integration venv: ``python graph_seed.py spec.json`` with the same
environment the services get (``PythonServices.env``). The provider is the one the services use, so the same script serves Neo4j and
Arango and the data has the product's own shape. The result is printed as one JSON line.

Spec: ``{"ops": [{"op": "...", ...}, ...]}``. Operations:

* ``bulk_users``  ``{org_id, count, prefix}``: filler users (graph only), each on the org's All team as a reader.
* ``legacy_agents`` ``{org_id, count, created_by}``: agent nodes with no ``handle`` (what ``agent_handles_v1`` backfills).
* ``reader_edge`` ``{user_key, record_id}``: a legacy user READER edge onto a record (what the PH-07 cleanup removes).
* ``agents`` ``{org_id}``: every agent node of the org (id, name, handle, updatedAtTimestamp).
* ``reader_edges`` ``{record_id}``: the PERMISSION edges onto a record, as roles.
* ``user_key`` ``{user_id}``: the graph key of a user by their Mongo id.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid


async def provider():  # noqa: ANN201
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.encrypted_store import EncryptedKeyValueStore
    from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory

    logger = logging.getLogger("graph_seed")
    logging.disable(logging.CRITICAL)
    config = ConfigurationService(logger, EncryptedKeyValueStore(logger))
    return await GraphDBProviderFactory.create_provider(logger=logger, config_service=config)


async def run(spec: dict) -> list:
    from app.config.constants.arangodb import CollectionNames

    graph = await provider()
    out: list = []
    now = int(time.time() * 1000)
    for op in spec["ops"]:
        kind = op["op"]
        if kind == "bulk_users":
            org, prefix, team = op["org_id"], op.get("prefix", "filler"), f"all_{op['org_id']}"
            users = [
                {
                    "id": str(uuid.uuid4()),
                    "userId": f"{prefix}-{i}",
                    "orgId": org,
                    "email": f"{prefix}-{i}@filler.example",
                    "fullName": f"Filler {i}",
                    "isActive": True,
                    "createdAtTimestamp": now,
                    "updatedAtTimestamp": now,
                }
                for i in range(op["count"])
            ]
            for start in range(0, len(users), 500):
                chunk = users[start : start + 500]
                await graph.batch_upsert_nodes(chunk, CollectionNames.USERS.value)
                await graph.batch_create_edges(
                    [{"from_id": u["id"], "from_collection": CollectionNames.USERS.value, "to_id": org, "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION", "createdAtTimestamp": now} for u in chunk],
                    CollectionNames.BELONGS_TO.value,
                )
                await graph.batch_create_edges(
                    [{"from_id": u["id"], "from_collection": CollectionNames.USERS.value, "to_id": team, "to_collection": CollectionNames.TEAMS.value, "type": "USER", "role": "READER", "createdAtTimestamp": now, "updatedAtTimestamp": now} for u in chunk],
                    CollectionNames.PERMISSION.value,
                )
            out.append({"op": kind, "count": len(users)})
        elif kind == "legacy_agents":
            agents = [
                {
                    "id": str(uuid.uuid4()),
                    "name": "Legacy agent",
                    "description": "created before handles existed",
                    "startMessage": "Hello",
                    "systemPrompt": "Be brief.",
                    "models": [],
                    "orgId": op["org_id"],
                    "createdBy": op.get("created_by", "seed"),
                    "createdAtTimestamp": now - 1000 * (op["count"] - i),
                    "updatedAtTimestamp": now - 1000 * (op["count"] - i),
                    "isDeleted": False,
                }
                for i in range(op["count"])
            ]
            await graph.batch_upsert_nodes(agents, CollectionNames.AGENT_INSTANCES.value)
            out.append({"op": kind, "ids": [a["id"] for a in agents]})
        elif kind == "reader_edge":
            await graph.batch_create_edges(
                [{"from_id": op["user_key"], "from_collection": CollectionNames.USERS.value, "to_id": op["record_id"], "to_collection": CollectionNames.RECORDS.value, "type": "USER", "role": "READER", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
                CollectionNames.PERMISSION.value,
            )
            out.append({"op": kind})
        elif kind == "agents":
            rows = await graph.get_nodes_by_filters(collection=CollectionNames.AGENT_INSTANCES.value, filters={"orgId": op["org_id"]})
            out.append(
                [
                    {"id": r.get("id") or r.get("_key"), "name": r.get("name"), "handle": r.get("handle"), "updatedAtTimestamp": r.get("updatedAtTimestamp")}
                    for r in rows or []
                ]
            )
        elif kind == "reader_edges":
            edges = await graph.get_edges_to_node(f"{CollectionNames.RECORDS.value}/{op['record_id']}", CollectionNames.PERMISSION.value)
            out.append([{"role": e.get("role"), "type": e.get("type"), "from": e.get("from_id") or e.get("_from")} for e in edges or []])
        elif kind == "user_key":
            user = await graph.get_user_by_user_id(op["user_id"])
            out.append((user or {}).get("_key") or (user or {}).get("id"))
        else:
            raise ValueError(f"unknown op {kind!r}")
    return out


def main() -> None:
    with open(sys.argv[1]) as handle:
        spec = json.load(handle)
    result = asyncio.run(run(spec))
    sys.stdout.write("\nGRAPH_SEED_RESULT " + json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
