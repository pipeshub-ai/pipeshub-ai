"""A graph and a configuration store holding agents, as the migration reads them (P2.2)."""

from __future__ import annotations

import copy
from typing import Any

from app.config.constants.arangodb import CollectionNames

AGENTS = CollectionNames.AGENT_INSTANCES.value
TOOLSETS = CollectionNames.AGENT_TOOLSETS.value
HAS_TOOLSET = CollectionNames.AGENT_HAS_TOOLSET.value
USERS = CollectionNames.USERS.value
ORG = "org-1"


class FakeGraph:
    """The ``IGraphDBProvider`` calls the migration makes; a replace drops absent fields, as both backends do."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {AGENTS: {}, TOOLSETS: {}, USERS: {}}
        self.edges: list[dict[str, str]] = []
        self.writes = 0
        self.before_write: Any = None

    def agent(self, key: str, *toolsets: dict[str, Any], user: str = "u-1", org: str | None = ORG) -> None:
        self.collections[AGENTS][key] = {"_key": key, "name": key, "createdBy": user, "isDeleted": False}
        if org is not None:
            self.collections[USERS][user] = {"_key": user, "orgId": org}
        for toolset in toolsets:
            self.collections[TOOLSETS][toolset["_key"]] = {
                "name": "jira", "displayName": "Jira", "type": "app", "userId": user, "createdBy": user,
                "createdAtTimestamp": 1, "updatedAtTimestamp": 1, **toolset,
            }
            self.edges.append({"_from": f"{AGENTS}/{key}", "_to": f"{TOOLSETS}/{toolset['_key']}"})

    async def get_documents_paginated(self, collection: str, skip: int = 0, limit: int = 50, **_: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        docs = [copy.deepcopy(d) for _, d in sorted(self.collections[collection].items())]
        return docs[skip : skip + limit]

    async def get_edges_from_node(self, node_id: str, edge_collection: str, transaction: str | None = None) -> list[dict[str, str]]:
        assert edge_collection == HAS_TOOLSET
        return [dict(e) for e in self.edges if e["_from"] == node_id]

    async def get_document(
        self, key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False,
    ) -> dict[str, Any] | None:
        found = self.collections[collection].get(key)
        return copy.deepcopy(found) if found is not None else None

    async def update_node_if_match(
        self, key: str, collection: str, node: dict[str, Any], match_field: str, match_value: Any, transaction: str | None = None  # noqa: ANN401
    ) -> bool:
        if self.before_write is not None:
            self.before_write(key)
        stored = self.collections[collection].get(key)
        if stored is None or stored.get(match_field) != match_value:
            return False
        self.collections[collection][key] = {**copy.deepcopy(node), "_key": key}
        self.writes += 1
        return True


class FakeConfig:
    def __init__(self, instances: list[dict[str, Any]]) -> None:
        self.values: dict[str, Any] = {"/services/toolset-instances": instances}
        self.sets = 0

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.values[key] = copy.deepcopy(value)
        self.sets += 1
        return True


def instance(instance_id: str, toolset_type: str = "jira", org: str = ORG) -> dict[str, Any]:
    return {"_id": instance_id, "toolsetType": toolset_type, "instanceName": f"{toolset_type} {instance_id}", "orgId": org}


def toolset(key: str, *, name: str = "jira", instance_id: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"_key": key, "name": name}
    if instance_id is not None:
        entry["instanceId"] = instance_id
    return entry
