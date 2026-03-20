"""
Connector Instance Deletion - Integration Test
================================================

Tests delete_connector_instance() against a live graph database.
Backend is selected via the DATA_STORE env var:
    neo4j    -> Neo4jProvider
    arangodb -> ArangoHTTPProvider

Creates 3 connector apps with realistic overlapping graph data, deletes each
one individually, and verifies the remaining two are fully intact.

How to run
----------
1. From the repo root, go to the Python backend:
       cd backend/python

2. Activate the virtual environment:
       source venv/bin/activate          # Linux/macOS
       .\\venv\\Scripts\\activate        # Windows

3. Ensure .env exists in backend/python/ with the required variables
   (see below). The script loads it via load_dotenv() from the current
   working directory, so you must run the script from backend/python.

4. Run the integration test:
       python -m app.scripts.connector_deletion_integration_test

Required env vars (in backend/python/.env):
    DATA_STORE       - "neo4j" or "arangodb" (default: arangodb)

    For Neo4j:
        NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE

    For ArangoDB:
        Configured via ConfigurationService (/services/arangodb key)
        or ARANGODB_HOST, ARANGODB_PORT, ARANGODB_USERNAME,
        ARANGODB_PASSWORD, ARANGODB_DATABASE

Test Coverage:
    SETUP       Seed shared entities (org, users, departments, categories,
                topics, languages) + 3 connector apps with records,
                record groups, roles, groups, and all edge types.
    TC-DEL-001  Delete Connector A -> verify B & C intact
    TC-DEL-002  Delete Connector B -> verify A & C intact
    TC-DEL-003  Delete Connector C -> verify A & B intact
    CLEANUP     Best-effort removal of all test data.
"""

import asyncio
import contextlib
import logging
import os
import sys
import time as _time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# Load .env from current working directory (run from backend/python/)
load_dotenv()

from app.config.configuration_service import ConfigurationService  # noqa: E402
from app.config.constants.arangodb import CollectionNames  # noqa: E402
from app.config.providers.in_memory_store import InMemoryKeyValueStore  # noqa: E402
from app.services.graph_db.graph_db_provider_factory import (  # noqa: E402
    GraphDBProviderFactory,
)
from app.services.graph_db.interface.graph_db_provider import (  # noqa: E402
    IGraphDBProvider,
)
from app.utils.logger import create_logger  # noqa: E402


class TestKeyValueStore(InMemoryKeyValueStore):
    """Concrete subclass that adds the missing ``client`` property."""

    @property
    def client(self) -> None:
        return None


# -- Logging -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = create_logger("connector-deletion-test")

# -- Run-scoped identifiers ----------------------------------------------------
_RUN_ID = uuid.uuid4().hex[:8]
ORG_ID = f"test-org-{_RUN_ID}"

CONNECTOR_IDS = [f"conn-{label}-{_RUN_ID}" for label in ("A", "B", "C")]
CONN_A, CONN_B, CONN_C = CONNECTOR_IDS

MAX_FAILURES_IN_DETAIL = 10

USER_IDS = [f"user-{i}-{_RUN_ID}" for i in range(1, 4)]
USER_EMAILS = [f"user{i}_{_RUN_ID}@test.com" for i in range(1, 4)]

DEPT_ID = f"dept-eng-{_RUN_ID}"
CATEGORY_ID = f"cat-docs-{_RUN_ID}"
TOPIC_ID = f"topic-api-{_RUN_ID}"
LANGUAGE_ID = f"lang-en-{_RUN_ID}"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_results: list[dict] = []


def _record(name: str, status: str, detail: str = "") -> None:
    icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[status]
    _results.append({"name": name, "status": status, "detail": detail})
    suffix = f"  --  {detail}" if detail else ""
    logger.info(f"{icon}  [{status}]  {name}{suffix}")


# ==============================================================================
# QUERY HELPERS  (abstract away Neo4j Cypher vs ArangoDB AQL)
# ==============================================================================

class QueryHelper(ABC):
    """Backend-specific raw queries used only in cleanup & verification."""

    @abstractmethod
    async def cleanup_connector_nodes(self, connector_id: str) -> None:
        """Remove all nodes with connectorId + the App node."""

    @abstractmethod
    async def cleanup_isoftype_target(self, target_id: str, collection: str) -> None:
        """Remove a single isOfType target node."""

    @abstractmethod
    async def find_orphan_nodes(self, connector_id: str) -> List[Dict]:
        """Return list of {label, id} for nodes still carrying this connectorId."""

    @abstractmethod
    async def find_orphan_app_edges(self, connector_id: str) -> List[Dict]:
        """Return list of {rel_type, cnt} for edges still connected to the app."""

    @abstractmethod
    async def count_nodes_by_label(self, connector_id: str) -> Dict[str, int]:
        """Return {label: count} of nodes with this connectorId."""

    @abstractmethod
    async def count_edges(
        self, edge_type: str, connector_id: str, direction: str
    ) -> int:
        """Count edges of a given type related to this connector."""


class Neo4jQueryHelper(QueryHelper):
    """Cypher-based queries for Neo4j."""

    def __init__(self, provider: IGraphDBProvider) -> None:
        self._client = provider.client  # type: ignore[attr-defined]

    async def cleanup_connector_nodes(self, connector_id: str) -> None:
        await self._client.execute_query(
            "MATCH (n) WHERE n.connectorId = $cid DETACH DELETE n",
            parameters={"cid": connector_id},
        )
        await self._client.execute_query(
            "MATCH (n:App {id: $cid}) DETACH DELETE n",
            parameters={"cid": connector_id},
        )

    async def cleanup_isoftype_target(self, target_id: str, collection: str) -> None:
        from app.config.constants.neo4j import collection_to_label

        label = collection_to_label(collection)
        await self._client.execute_query(
            f"MATCH (n:{label} {{id: $id}}) DETACH DELETE n",
            parameters={"id": target_id},
        )

    async def find_orphan_nodes(self, connector_id: str) -> List[Dict]:
        rows = await self._client.execute_query(
            "MATCH (n) WHERE n.connectorId = $cid "
            "RETURN labels(n)[0] AS label, n.id AS id LIMIT 20",
            parameters={"cid": connector_id},
        )
        return rows or []

    async def find_orphan_app_edges(self, connector_id: str) -> List[Dict]:
        rows = await self._client.execute_query(
            "MATCH (a:App {id: $cid})-[r]-() "
            "RETURN type(r) AS rel_type, count(r) AS cnt",
            parameters={"cid": connector_id},
        )
        return rows or []

    async def count_nodes_by_label(self, connector_id: str) -> Dict[str, int]:
        rows = await self._client.execute_query(
            "MATCH (n) WHERE n.connectorId = $cid "
            "RETURN labels(n)[0] AS label, count(n) AS cnt",
            parameters={"cid": connector_id},
        )
        return {r["label"]: r["cnt"] for r in (rows or [])}

    async def count_edges(
        self, edge_type: str, connector_id: str, direction: str
    ) -> int:
        if edge_type == "USER_APP_RELATION":
            q = (
                "MATCH (:User)-[r:USER_APP_RELATION]->(:App {id: $cid}) "
                "RETURN count(r) AS cnt"
            )
        elif edge_type == "ORG_APP_RELATION":
            q = (
                "MATCH (:Organization)-[r:ORG_APP_RELATION]->(:App {id: $cid}) "
                "RETURN count(r) AS cnt"
            )
        elif edge_type == "IS_OF_TYPE":
            q = (
                "MATCH (r:Record {connectorId: $cid})-[e:IS_OF_TYPE]->() "
                "RETURN count(e) AS cnt"
            )
        elif edge_type == "PERMISSION":
            q = (
                "MATCH (role:Role {connectorId: $cid})-[p:PERMISSION]->() "
                "RETURN count(p) AS cnt"
            )
        else:
            return 0
        rows = await self._client.execute_query(q, parameters={"cid": connector_id})
        return rows[0]["cnt"] if rows else 0


class ArangoQueryHelper(QueryHelper):
    """AQL-based queries for ArangoDB."""

    def __init__(self, provider: IGraphDBProvider) -> None:
        self._client = provider.http_client  # type: ignore[attr-defined]

    async def cleanup_connector_nodes(self, connector_id: str) -> None:
        # Delete nodes with connectorId across relevant collections
        for coll in [
            CollectionNames.RECORDS.value,
            CollectionNames.RECORD_GROUPS.value,
            CollectionNames.ROLES.value,
            CollectionNames.GROUPS.value,
        ]:
            await self._client.execute_aql(
                "FOR doc IN @@coll FILTER doc.connectorId == @cid REMOVE doc IN @@coll",
                bind_vars={"@coll": coll, "cid": connector_id},
            )
        # Delete the App node
        await self._client.execute_aql(
            "FOR doc IN @@coll FILTER doc._key == @cid REMOVE doc IN @@coll",
            bind_vars={"@coll": CollectionNames.APPS.value, "cid": connector_id},
        )
        # Delete edges referencing deleted nodes (scan all edge collections)
        edge_collections = [
            CollectionNames.ORG_APP_RELATION.value,
            CollectionNames.USER_APP_RELATION.value,
            CollectionNames.BELONGS_TO_RECORD_GROUP.value,
            CollectionNames.PERMISSION.value,
            CollectionNames.RECORD_RELATIONS.value,
            CollectionNames.IS_OF_TYPE.value,
            CollectionNames.INHERIT_PERMISSIONS.value,
            CollectionNames.BELONGS_TO_DEPARTMENT.value,
            CollectionNames.BELONGS_TO_CATEGORY.value,
            CollectionNames.BELONGS_TO_TOPIC.value,
            CollectionNames.BELONGS_TO_LANGUAGE.value,
        ]
        app_id = f"{CollectionNames.APPS.value}/{connector_id}"
        for ecoll in edge_collections:
            with contextlib.suppress(Exception):
                await self._client.execute_aql(
                    "FOR e IN @@ecoll "
                    "FILTER e._from == @app_id OR e._to == @app_id "
                    "OR STARTS_WITH(e._from, @prefix) OR STARTS_WITH(e._to, @prefix) "
                    "REMOVE e IN @@ecoll",
                    bind_vars={
                        "@ecoll": ecoll,
                        "app_id": app_id,
                        "prefix": f"{CollectionNames.RECORDS.value}/rec-",
                    },
                )

    async def cleanup_isoftype_target(self, target_id: str, collection: str) -> None:
        with contextlib.suppress(Exception):
            await self._client.execute_aql(
                "FOR doc IN @@coll FILTER doc._key == @id REMOVE doc IN @@coll",
                bind_vars={"@coll": collection, "id": target_id},
            )

    async def find_orphan_nodes(self, connector_id: str) -> List[Dict]:
        results: List[Dict] = []
        for coll in [
            CollectionNames.RECORDS.value,
            CollectionNames.RECORD_GROUPS.value,
            CollectionNames.ROLES.value,
            CollectionNames.GROUPS.value,
        ]:
            rows = await self._client.execute_aql(
                "FOR doc IN @@coll FILTER doc.connectorId == @cid "
                "LIMIT 10 RETURN {label: @coll_name, id: doc._key}",
                bind_vars={"@coll": coll, "cid": connector_id, "coll_name": coll},
            )
            results.extend(rows or [])
        return results

    async def find_orphan_app_edges(self, connector_id: str) -> List[Dict]:
        # If app node is already deleted, no edges can reference it in ArangoDB
        app_id = f"{CollectionNames.APPS.value}/{connector_id}"
        results: List[Dict] = []
        edge_collections = [
            CollectionNames.ORG_APP_RELATION.value,
            CollectionNames.USER_APP_RELATION.value,
        ]
        for ecoll in edge_collections:
            try:
                rows = await self._client.execute_aql(
                    "FOR e IN @@ecoll "
                    "FILTER e._from == @app_id OR e._to == @app_id "
                    "COLLECT WITH COUNT INTO cnt "
                    "RETURN {rel_type: @ecoll_name, cnt: cnt}",
                    bind_vars={
                        "@ecoll": ecoll,
                        "app_id": app_id,
                        "ecoll_name": ecoll,
                    },
                )
                results.extend(r for r in (rows or []) if r["cnt"] > 0)
            except Exception:
                pass
        return results

    async def count_nodes_by_label(self, connector_id: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        label_map = {
            CollectionNames.RECORDS.value: "Record",
            CollectionNames.RECORD_GROUPS.value: "RecordGroup",
            CollectionNames.ROLES.value: "Role",
            CollectionNames.GROUPS.value: "Group",
        }
        for coll, label in label_map.items():
            rows = await self._client.execute_aql(
                "FOR doc IN @@coll FILTER doc.connectorId == @cid "
                "COLLECT WITH COUNT INTO cnt RETURN cnt",
                bind_vars={"@coll": coll, "cid": connector_id},
            )
            cnt = rows[0] if rows else 0
            if cnt > 0:
                counts[label] = cnt
        return counts

    async def count_edges(
        self, edge_type: str, connector_id: str, direction: str
    ) -> int:
        if edge_type == "USER_APP_RELATION":
            app_id = f"{CollectionNames.APPS.value}/{connector_id}"
            rows = await self._client.execute_aql(
                "FOR e IN @@ecoll FILTER e._to == @app_id "
                "COLLECT WITH COUNT INTO cnt RETURN cnt",
                bind_vars={
                    "@ecoll": CollectionNames.USER_APP_RELATION.value,
                    "app_id": app_id,
                },
            )
        elif edge_type == "ORG_APP_RELATION":
            app_id = f"{CollectionNames.APPS.value}/{connector_id}"
            rows = await self._client.execute_aql(
                "FOR e IN @@ecoll FILTER e._to == @app_id "
                "COLLECT WITH COUNT INTO cnt RETURN cnt",
                bind_vars={
                    "@ecoll": CollectionNames.ORG_APP_RELATION.value,
                    "app_id": app_id,
                },
            )
        elif edge_type == "IS_OF_TYPE":
            rows = await self._client.execute_aql(
                "FOR r IN @@records FILTER r.connectorId == @cid "
                "FOR e IN @@ecoll FILTER e._from == CONCAT(@rec_coll, '/', r._key) "
                "COLLECT WITH COUNT INTO cnt RETURN cnt",
                bind_vars={
                    "@records": CollectionNames.RECORDS.value,
                    "@ecoll": CollectionNames.IS_OF_TYPE.value,
                    "cid": connector_id,
                    "rec_coll": CollectionNames.RECORDS.value,
                },
            )
        elif edge_type == "PERMISSION":
            rows = await self._client.execute_aql(
                "FOR role IN @@roles FILTER role.connectorId == @cid "
                "FOR e IN @@ecoll FILTER e._from == CONCAT(@role_coll, '/', role._key) "
                "COLLECT WITH COUNT INTO cnt RETURN cnt",
                bind_vars={
                    "@roles": CollectionNames.ROLES.value,
                    "@ecoll": CollectionNames.PERMISSION.value,
                    "cid": connector_id,
                    "role_coll": CollectionNames.ROLES.value,
                },
            )
        else:
            return 0
        return rows[0] if rows else 0


def _create_query_helper(provider: IGraphDBProvider) -> QueryHelper:
    """Factory: pick the right QueryHelper based on DATA_STORE."""
    data_store = os.getenv("DATA_STORE", "arangodb").lower()
    if data_store == "neo4j":
        return Neo4jQueryHelper(provider)
    return ArangoQueryHelper(provider)


# ==============================================================================
# DATA DEFINITIONS
# ==============================================================================

def _build_connector_data(connector_id: str, connector_label: str) -> Dict[str, Any]:
    """
    Build all node and edge data for a single connector instance.
    Each connector gets:
      - 1 app
      - 2 record groups
      - 4 records (type mix varies by connector)
      - 2 roles (OWNER, READER)
      - 1 group
      - isOfType targets
      - All edge types
    """
    ts = int(_time.time() * 1000)
    prefix = f"{connector_label.lower()}-{_RUN_ID}"

    # -- App (fields must match ArangoDB app schema; orgId omitted for schema compatibility) --
    app = {
        "id": connector_id,
        "_key": connector_id,
        "name": f"Test Connector {connector_label}",
        "type": "S3",
        "appGroup": "S3",
        "authType": "ACCESS_KEY",
        "scope": "team",
        "isActive": True,
        "isAgentActive": False,
        "isConfigured": True,
        "isAuthenticated": True,
        "createdBy": USER_EMAILS[0],
        "updatedBy": None,
        "createdAtTimestamp": ts,
        "updatedAtTimestamp": ts,
    }

    # -- Record Groups (fields must match ArangoDB record_group_schema) --------
    rg_ids = [f"rg-{prefix}-{i}" for i in range(1, 3)]
    record_groups = [
        {
            "id": rg_id,
            "_key": rg_id,
            "connectorId": connector_id,
            "connectorName": "S3",
            "orgId": ORG_ID,
            "externalGroupId": f"ext-{rg_id}",
            "groupName": f"Group {i} ({connector_label})",
            "groupType": "BUCKET",
            "createdAtTimestamp": ts,
            "updatedAtTimestamp": ts,
            "lastSyncTimestamp": ts,
        }
        for i, rg_id in enumerate(rg_ids, start=1)
    ]

    # -- Records ---------------------------------------------------------------
    record_ids = [f"rec-{prefix}-{i}" for i in range(1, 5)]
    type_map = {"A": "FILE", "B": "MAIL", "C": "WEBPAGE"}
    primary_type = type_map.get(connector_label, "FILE")
    record_types = [primary_type, primary_type, primary_type, "FILE"]

    # Records: fields must match ArangoDB record_schema (additionalProperties: False)
    records = [
        {
            "id": rec_id,
            "_key": rec_id,
            "connectorId": connector_id,
            "connectorName": "S3",
            "orgId": ORG_ID,
            "externalRecordId": f"ext-{rec_id}",
            "recordName": f"Record {i} ({connector_label})",
            "recordType": record_types[i - 1],
            "mimeType": "application/pdf",
            "origin": "CONNECTOR",
            "isDirty": False,
            "version": 1,
            "virtualRecordId": f"vr-{rec_id}",
            "indexingStatus": "COMPLETED",
            "createdAtTimestamp": ts,
            "updatedAtTimestamp": ts,
        }
        for i, rec_id in enumerate(record_ids, start=1)
    ]

    # -- Roles (fields must match ArangoDB app_role_schema) --------------------
    role_ids = [f"role-{prefix}-owner", f"role-{prefix}-reader"]
    roles = [
        {
            "id": role_ids[0], "_key": role_ids[0],
            "connectorId": connector_id, "connectorName": "S3", "orgId": ORG_ID,
            "name": "OWNER", "externalRoleId": f"ext-{role_ids[0]}",
            "createdAtTimestamp": ts, "updatedAtTimestamp": ts, "lastSyncTimestamp": ts,
        },
        {
            "id": role_ids[1], "_key": role_ids[1],
            "connectorId": connector_id, "connectorName": "S3", "orgId": ORG_ID,
            "name": "READER", "externalRoleId": f"ext-{role_ids[1]}",
            "createdAtTimestamp": ts, "updatedAtTimestamp": ts, "lastSyncTimestamp": ts,
        },
    ]

    # -- Groups ----------------------------------------------------------------
    group_ids = [f"grp-{prefix}-1"]
    groups = [
        {
            "id": group_ids[0], "_key": group_ids[0],
            "connectorId": connector_id, "orgId": ORG_ID,
            "name": f"Team ({connector_label})", "isActive": True,
            "createdAtTimestamp": ts, "updatedAtTimestamp": ts,
        }
    ]

    # -- isOfType targets (doc shape must match each collection's schema) -------
    type_collection_map = {
        "FILE": CollectionNames.FILES.value,
        "MAIL": CollectionNames.MAILS.value,
        "WEBPAGE": CollectionNames.WEBPAGES.value,
    }
    isoftype_targets = []
    for i, rec_id in enumerate(record_ids):
        rtype = record_types[i]
        target_collection = type_collection_map.get(rtype, CollectionNames.FILES.value)
        target_id = f"type-{rec_id}"
        if target_collection == CollectionNames.FILES.value:
            doc = {"id": target_id, "name": f"file-{rec_id}", "isFile": True, "orgId": ORG_ID}
        elif target_collection == CollectionNames.MAILS.value:
            doc = {"id": target_id, "threadId": f"thread-{target_id}", "isParent": False}
        else:
            doc = {"id": target_id, "orgId": ORG_ID}
        isoftype_targets.append({
            "doc": doc,
            "collection": target_collection,
            "record_id": rec_id,
            "target_id": target_id,
        })

    # -- Edges -----------------------------------------------------------------
    edges: List[Dict] = []

    # orgAppRelation: org -> app
    edges.append({
        "edge": {
            "_from": f"{CollectionNames.ORGS.value}/{ORG_ID}",
            "_to": f"{CollectionNames.APPS.value}/{connector_id}",
            "from_id": ORG_ID, "from_collection": CollectionNames.ORGS.value,
            "to_id": connector_id, "to_collection": CollectionNames.APPS.value,
            "createdAtTimestamp": ts,
        },
        "collection": CollectionNames.ORG_APP_RELATION.value,
    })

    # userAppRelation: each user -> app (schema requires syncState, lastSyncUpdate)
    edges.extend(
        [
            {
                "edge": {
                    "_from": f"{CollectionNames.USERS.value}/{uid}",
                    "_to": f"{CollectionNames.APPS.value}/{connector_id}",
                    "from_id": uid, "from_collection": CollectionNames.USERS.value,
                    "to_id": connector_id, "to_collection": CollectionNames.APPS.value,
                    "syncState": "COMPLETED",
                    "lastSyncUpdate": ts,
                    "createdAtTimestamp": ts,
                },
                "collection": CollectionNames.USER_APP_RELATION.value,
            }
            for uid in USER_IDS
        ]
    )

    # belongsToRecordGroup: record -> recordGroup
    for i, rec_id in enumerate(record_ids):
        rg_id = rg_ids[i % len(rg_ids)]
        edges.append({
            "edge": {
                "_from": f"{CollectionNames.RECORDS.value}/{rec_id}",
                "_to": f"{CollectionNames.RECORD_GROUPS.value}/{rg_id}",
                "from_id": rec_id, "from_collection": CollectionNames.RECORDS.value,
                "to_id": rg_id, "to_collection": CollectionNames.RECORD_GROUPS.value,
                "createdAtTimestamp": ts,
            },
            "collection": CollectionNames.BELONGS_TO_RECORD_GROUP.value,
        })

    # permission: role -> recordGroup, role -> first two records
    edges.extend(
        [
            {
                "edge": {
                    "_from": f"{CollectionNames.ROLES.value}/{role_id}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{rg_id}",
                    "from_id": role_id, "from_collection": CollectionNames.ROLES.value,
                    "to_id": rg_id, "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "createdAtTimestamp": ts,
                },
                "collection": CollectionNames.PERMISSION.value,
            }
            for role_id in role_ids
            for rg_id in rg_ids
        ]
    )
    edges.extend(
        [
            {
                "edge": {
                    "_from": f"{CollectionNames.ROLES.value}/{role_id}",
                    "_to": f"{CollectionNames.RECORDS.value}/{rec_id}",
                    "from_id": role_id, "from_collection": CollectionNames.ROLES.value,
                    "to_id": rec_id, "to_collection": CollectionNames.RECORDS.value,
                    "createdAtTimestamp": ts,
                },
                "collection": CollectionNames.PERMISSION.value,
            }
            for role_id in role_ids
            for rec_id in record_ids[:2]
        ]
    )

    # recordRelations: parent-child (rec1->rec2, rec3->rec4)
    for parent, child in [(record_ids[0], record_ids[1]), (record_ids[2], record_ids[3])]:
        edges.append({
            "edge": {
                "_from": f"{CollectionNames.RECORDS.value}/{parent}",
                "_to": f"{CollectionNames.RECORDS.value}/{child}",
                "from_id": parent, "from_collection": CollectionNames.RECORDS.value,
                "to_id": child, "to_collection": CollectionNames.RECORDS.value,
                "relationType": "PARENT_CHILD", "createdAtTimestamp": ts,
            },
            "collection": CollectionNames.RECORD_RELATIONS.value,
        })

    # isOfType: record -> type node
    edges.extend(
        [
            {
                "edge": {
                    "_from": f"{CollectionNames.RECORDS.value}/{t['record_id']}",
                    "_to": f"{t['collection']}/{t['target_id']}",
                    "from_id": t["record_id"], "from_collection": CollectionNames.RECORDS.value,
                    "to_id": t["target_id"], "to_collection": t["collection"],
                    "createdAtTimestamp": ts,
                },
                "collection": CollectionNames.IS_OF_TYPE.value,
            }
            for t in isoftype_targets
        ]
    )

    # inheritPermissions: rg1 -> rg2
    edges.append({
        "edge": {
            "_from": f"{CollectionNames.RECORD_GROUPS.value}/{rg_ids[0]}",
            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{rg_ids[1]}",
            "from_id": rg_ids[0], "from_collection": CollectionNames.RECORD_GROUPS.value,
            "to_id": rg_ids[1], "to_collection": CollectionNames.RECORD_GROUPS.value,
            "createdAtTimestamp": ts,
        },
        "collection": CollectionNames.INHERIT_PERMISSIONS.value,
    })

    # Classification edges (shared nodes)
    classification_edges = [
        (record_ids[0], DEPT_ID, CollectionNames.DEPARTMENTS.value,
         CollectionNames.BELONGS_TO_DEPARTMENT.value),
        (record_ids[1], CATEGORY_ID, CollectionNames.CATEGORIES.value,
         CollectionNames.BELONGS_TO_CATEGORY.value),
        (record_ids[2], TOPIC_ID, CollectionNames.TOPICS.value,
         CollectionNames.BELONGS_TO_TOPIC.value),
        (record_ids[0], LANGUAGE_ID, CollectionNames.LANGUAGES.value,
         CollectionNames.BELONGS_TO_LANGUAGE.value),
    ]
    for rec_id, target_id, target_coll, edge_coll in classification_edges:
        edges.append({
            "edge": {
                "_from": f"{CollectionNames.RECORDS.value}/{rec_id}",
                "_to": f"{target_coll}/{target_id}",
                "from_id": rec_id, "from_collection": CollectionNames.RECORDS.value,
                "to_id": target_id, "to_collection": target_coll,
                "createdAtTimestamp": ts,
            },
            "collection": edge_coll,
        })

    return {
        "app": app,
        "record_groups": record_groups,
        "records": records,
        "roles": roles,
        "groups": groups,
        "isoftype_targets": isoftype_targets,
        "edges": edges,
        "record_ids": record_ids,
        "rg_ids": rg_ids,
        "role_ids": role_ids,
        "group_ids": group_ids,
    }


# ==============================================================================
# SEEDING
# ==============================================================================

async def seed_shared_entities(provider: IGraphDBProvider) -> None:
    """Seed org, users, and shared classification nodes."""
    ts = int(_time.time() * 1000)

    await provider.batch_upsert_nodes(
        [{"id": ORG_ID, "_key": ORG_ID, "name": "Test Org", "accountType": "enterprise",
          "isActive": True, "createdAtTimestamp": ts, "updatedAtTimestamp": ts}],
        CollectionNames.ORGS.value,
    )

    user_nodes = [
        {"id": uid, "_key": uid, "email": email, "userId": uid, "orgId": ORG_ID,
         "isActive": True, "createdAtTimestamp": ts, "updatedAtTimestamp": ts}
        for uid, email in zip(USER_IDS, USER_EMAILS)
    ]
    await provider.batch_upsert_nodes(user_nodes, CollectionNames.USERS.value)

    bt_edges = [
        {
            "_from": f"{CollectionNames.USERS.value}/{uid}",
            "_to": f"{CollectionNames.ORGS.value}/{ORG_ID}",
            "from_id": uid, "from_collection": CollectionNames.USERS.value,
            "to_id": ORG_ID, "to_collection": CollectionNames.ORGS.value,
            "entityType": "ORGANIZATION", "createdAtTimestamp": ts,
        }
        for uid in USER_IDS
    ]
    await provider.batch_create_edges(bt_edges, CollectionNames.BELONGS_TO.value)

    # Department schema allows only departmentName and orgId (additionalProperties: False).
    # Provider translates "id" -> "_key" for ArangoDB; cleanup uses DEPT_ID as key.
    shared_nodes = [
        ([{"id": DEPT_ID, "departmentName": "Engineering (Test)", "orgId": ORG_ID}],
         CollectionNames.DEPARTMENTS.value),
        ([{"id": CATEGORY_ID, "_key": CATEGORY_ID, "categoryName": "Documentation (Test)",
           "createdAtTimestamp": ts, "updatedAtTimestamp": ts}],
         CollectionNames.CATEGORIES.value),
        ([{"id": TOPIC_ID, "_key": TOPIC_ID, "topicName": "API (Test)",
           "createdAtTimestamp": ts, "updatedAtTimestamp": ts}],
         CollectionNames.TOPICS.value),
        ([{"id": LANGUAGE_ID, "_key": LANGUAGE_ID, "languageName": "English (Test)",
           "createdAtTimestamp": ts, "updatedAtTimestamp": ts}],
         CollectionNames.LANGUAGES.value),
    ]
    for nodes, coll in shared_nodes:
        await provider.batch_upsert_nodes(nodes, coll)

    logger.info(
        f"Seeded shared entities: org={ORG_ID}, users={len(USER_IDS)}, "
        f"dept/cat/topic/lang=1 each"
    )


async def seed_connector(provider: IGraphDBProvider, data: Dict[str, Any]) -> None:
    """Seed a single connector's nodes and edges into the graph."""
    cid = data["app"]["id"]

    await provider.batch_upsert_nodes([data["app"]], CollectionNames.APPS.value)
    await provider.batch_upsert_nodes(data["record_groups"], CollectionNames.RECORD_GROUPS.value)
    await provider.batch_upsert_nodes(data["records"], CollectionNames.RECORDS.value)
    await provider.batch_upsert_nodes(data["roles"], CollectionNames.ROLES.value)
    await provider.batch_upsert_nodes(data["groups"], CollectionNames.GROUPS.value)

    targets_by_coll: Dict[str, List[Dict]] = defaultdict(list)
    for t in data["isoftype_targets"]:
        targets_by_coll[t["collection"]].append(t["doc"])
    for coll, docs in targets_by_coll.items():
        await provider.batch_upsert_nodes(docs, coll)

    edges_by_coll: Dict[str, List[Dict]] = defaultdict(list)
    for e in data["edges"]:
        edges_by_coll[e["collection"]].append(e["edge"])
    for coll, edge_list in edges_by_coll.items():
        await provider.batch_create_edges(edge_list, coll)

    logger.info(
        f"Seeded connector {cid}: records={len(data['records'])}, "
        f"rg={len(data['record_groups'])}, roles={len(data['roles'])}, "
        f"groups={len(data['groups'])}, edges={len(data['edges'])}, "
        f"isOfType={len(data['isoftype_targets'])}"
    )


async def seed_all_connectors(
    provider: IGraphDBProvider, all_data: Dict[str, Dict[str, Any]]
) -> None:
    await seed_shared_entities(provider)
    for data in all_data.values():
        await seed_connector(provider, data)


# ==============================================================================
# CLEANUP
# ==============================================================================

async def cleanup_all(
    provider: IGraphDBProvider, all_data: Dict[str, Dict[str, Any]]
) -> None:
    """Best-effort removal of all test data."""
    try:
        for cid, data in all_data.items():
            for rec in data["records"]:
                with contextlib.suppress(Exception):
                    await provider.delete_nodes([rec["id"]], CollectionNames.RECORDS.value)
            for rg in data["record_groups"]:
                with contextlib.suppress(Exception):
                    await provider.delete_nodes([rg["id"]], CollectionNames.RECORD_GROUPS.value)
            for role in data["roles"]:
                with contextlib.suppress(Exception):
                    await provider.delete_nodes([role["id"]], CollectionNames.ROLES.value)
            for grp in data["groups"]:
                with contextlib.suppress(Exception):
                    await provider.delete_nodes([grp["id"]], CollectionNames.GROUPS.value)
            for t in data["isoftype_targets"]:
                with contextlib.suppress(Exception):
                    await provider.delete_nodes([t["target_id"]], t["collection"])
            with contextlib.suppress(Exception):
                await provider.delete_nodes([cid], CollectionNames.APPS.value)

        for uid in USER_IDS:
            with contextlib.suppress(Exception):
                await provider.delete_nodes([uid], CollectionNames.USERS.value)
        with contextlib.suppress(Exception):
            await provider.delete_nodes([ORG_ID], CollectionNames.ORGS.value)
        for nid, coll in [
            (DEPT_ID, CollectionNames.DEPARTMENTS.value),
            (CATEGORY_ID, CollectionNames.CATEGORIES.value),
            (TOPIC_ID, CollectionNames.TOPICS.value),
            (LANGUAGE_ID, CollectionNames.LANGUAGES.value),
        ]:
            with contextlib.suppress(Exception):
                await provider.delete_nodes([nid], coll)

        logger.info("Cleanup completed (best-effort)")
    except Exception as exc:
        logger.warning(f"Cleanup error (non-fatal): {exc}")


async def cleanup_connector_data(
    provider: IGraphDBProvider,
    all_data: Dict[str, Dict[str, Any]],
    qh: QueryHelper,
) -> None:
    """Remove only connector-specific data -- leave shared entities."""
    for cid, data in all_data.items():
        with contextlib.suppress(Exception):
            await qh.cleanup_connector_nodes(cid)
        for t in data["isoftype_targets"]:
            with contextlib.suppress(Exception):
                await qh.cleanup_isoftype_target(t["target_id"], t["collection"])


# ==============================================================================
# SNAPSHOT & VALIDATION
# ==============================================================================

async def take_snapshot(
    provider: IGraphDBProvider, connector_id: str, data: Dict[str, Any]
) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "connector_id": connector_id,
        "record_ids": {r["id"] for r in data["records"]},
        "rg_ids": {rg["id"] for rg in data["record_groups"]},
        "role_ids": {r["id"] for r in data["roles"]},
        "group_ids": {g["id"] for g in data["groups"]},
        "isoftype_target_ids": {t["target_id"] for t in data["isoftype_targets"]},
        "edge_count": len(data["edges"]),
    }
    for rec_id in snapshot["record_ids"]:
        doc = await provider.get_document(rec_id, CollectionNames.RECORDS.value)
        if not doc:
            logger.warning(f"Snapshot: record {rec_id} not found in DB")
    return snapshot


async def verify_connector_deleted(
    provider: IGraphDBProvider,
    qh: QueryHelper,
    deleted_connector_id: str,
    deleted_data: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Verify all data for the deleted connector is gone."""
    failures: List[str] = []

    # 1. App node gone
    if await provider.get_document(deleted_connector_id, CollectionNames.APPS.value):
        failures.append(f"App node '{deleted_connector_id}' still exists")

    # 2-5. Records / RG / Roles / Groups gone
    checks = [
        (deleted_data["records"], CollectionNames.RECORDS.value, "Record"),
        (deleted_data["record_groups"], CollectionNames.RECORD_GROUPS.value, "RecordGroup"),
        (deleted_data["roles"], CollectionNames.ROLES.value, "Role"),
        (deleted_data["groups"], CollectionNames.GROUPS.value, "Group"),
    ]
    for items, coll, label in checks:
        for item in items:
            if await provider.get_document(item["id"], coll):
                failures.append(f"{label} '{item['id']}' still exists")  # noqa: PERF401

    # 6. isOfType targets gone
    for t in deleted_data["isoftype_targets"]:
        if await provider.get_document(t["target_id"], t["collection"]):
            failures.append(f"isOfType target '{t['target_id']}' ({t['collection']}) still exists")  # noqa: PERF401

    # 7. Bulk orphan-node check
    orphans = await qh.find_orphan_nodes(deleted_connector_id)
    for o in orphans:
        failures.append(f"Orphan node connectorId={deleted_connector_id}: {o['label']}(id={o['id']})")  # noqa: PERF401

    # 8. Orphan edges to deleted app
    orphan_edges = await qh.find_orphan_app_edges(deleted_connector_id)
    for e in orphan_edges:
        failures.append(f"Orphan edge {e['rel_type']} (count={e['cnt']}) to deleted app")  # noqa: PERF401

    return (len(failures) == 0, failures)


async def verify_connector_intact(
    provider: IGraphDBProvider,
    qh: QueryHelper,
    connector_id: str,
    data: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Verify surviving connector data is fully intact."""
    failures: List[str] = []

    # 1. App exists
    if not await provider.get_document(connector_id, CollectionNames.APPS.value):
        failures.append(f"App node '{connector_id}' missing")

    # 2-5. Records / RG / Roles / Groups exist
    checks = [
        (data["records"], CollectionNames.RECORDS.value, "Record"),
        (data["record_groups"], CollectionNames.RECORD_GROUPS.value, "RecordGroup"),
        (data["roles"], CollectionNames.ROLES.value, "Role"),
        (data["groups"], CollectionNames.GROUPS.value, "Group"),
    ]
    for items, coll, label in checks:
        for item in items:
            if not await provider.get_document(item["id"], coll):
                failures.append(f"{label} '{item['id']}' missing for {connector_id}")  # noqa: PERF401

    # 6. isOfType targets exist
    for t in data["isoftype_targets"]:
        if not await provider.get_document(t["target_id"], t["collection"]):
            failures.append(f"isOfType target '{t['target_id']}' missing for {connector_id}")  # noqa: PERF401

    # 7. Node counts by label
    actual = await qh.count_nodes_by_label(connector_id)
    expected_counts = {
        "Record": len(data["records"]),
        "RecordGroup": len(data["record_groups"]),
        "Role": len(data["roles"]),
        "Group": len(data["groups"]),
    }
    for label, exp in expected_counts.items():
        act = actual.get(label, 0)
        if act != exp:
            failures.append(f"{label} count for {connector_id}: expected {exp}, got {act}")

    # 8-11. Edge counts
    edge_checks = [
        ("USER_APP_RELATION", len(USER_IDS)),
        ("ORG_APP_RELATION", 1),
        ("IS_OF_TYPE", len(data["records"])),
        ("PERMISSION", len(data["role_ids"]) * (len(data["rg_ids"]) + 2)),
    ]
    for edge_type, expected in edge_checks:
        act = await qh.count_edges(edge_type, connector_id, "any")
        if act != expected:
            failures.append(f"{edge_type} for {connector_id}: expected {expected}, got {act}")

    return (len(failures) == 0, failures)


async def verify_shared_entities(provider: IGraphDBProvider) -> Tuple[bool, List[str]]:
    """Verify org, users, classification nodes still exist."""
    failures: List[str] = []

    if not await provider.get_document(ORG_ID, CollectionNames.ORGS.value):
        failures.append(f"Org '{ORG_ID}' deleted!")

    for uid in USER_IDS:
        if not await provider.get_document(uid, CollectionNames.USERS.value):
            failures.append(f"User '{uid}' deleted!")  # noqa: PERF401

    for nid, coll, label in [
        (DEPT_ID, CollectionNames.DEPARTMENTS.value, "Department"),
        (CATEGORY_ID, CollectionNames.CATEGORIES.value, "Category"),
        (TOPIC_ID, CollectionNames.TOPICS.value, "Topic"),
        (LANGUAGE_ID, CollectionNames.LANGUAGES.value, "Language"),
    ]:
        if not await provider.get_document(nid, coll):
            failures.append(f"{label} '{nid}' deleted!")

    return (len(failures) == 0, failures)


# ==============================================================================
# TEST CASES
# ==============================================================================

async def run_deletion_test(
    provider: IGraphDBProvider,
    qh: QueryHelper,
    all_data: Dict[str, Dict[str, Any]],
    delete_connector_id: str,
    surviving_connector_ids: List[str],
    test_name: str,
) -> None:
    try:
        # Snapshot survivors
        snapshots = {}
        for cid in surviving_connector_ids:
            snapshots[cid] = await take_snapshot(provider, cid, all_data[cid])

        # Delete
        logger.info(f"  Deleting connector {delete_connector_id}...")
        result = await provider.delete_connector_instance(
            connector_id=delete_connector_id, org_id=ORG_ID,
        )
        if not result.get("success", False):
            _record(test_name, FAIL, f"delete_connector_instance() failed: {result.get('error')}")
            return
        logger.info(f"  delete_connector_instance returned: {result}")

        # Verify deleted
        all_failures: List[str] = []
        ok, fails = await verify_connector_deleted(
            provider, qh, delete_connector_id, all_data[delete_connector_id]
        )
        if not ok:
            all_failures.extend(f"[DELETED {delete_connector_id}] {f}" for f in fails)

        # Verify survivors
        for cid in surviving_connector_ids:
            ok, fails = await verify_connector_intact(
                provider, qh, cid, all_data[cid], snapshots[cid]
            )
            if not ok:
                all_failures.extend(f"[SURVIVING {cid}] {f}" for f in fails)

        # Verify shared
        ok, fails = await verify_shared_entities(provider)
        if not ok:
            all_failures.extend(f"[SHARED] {f}" for f in fails)

        if all_failures:
            detail = "; ".join(all_failures[:MAX_FAILURES_IN_DETAIL])
            if len(all_failures) > MAX_FAILURES_IN_DETAIL:
                detail += f" ... and {len(all_failures) - MAX_FAILURES_IN_DETAIL} more"
            _record(test_name, FAIL, detail)
        else:
            _record(test_name, PASS,
                    f"Deleted {delete_connector_id}, survivors intact")

    except Exception as exc:
        _record(test_name, FAIL, f"Exception: {exc}")


# ==============================================================================
# MAIN
# ==============================================================================

async def run_all() -> None:
    data_store = os.getenv("DATA_STORE", "arangodb").lower()

    logger.info("=" * 72)
    logger.info("Connector Instance Deletion - Integration Test")
    logger.info(f"Run ID       : {_RUN_ID}")
    logger.info(f"Data Store   : {data_store}")
    logger.info(f"Org ID       : {ORG_ID}")
    logger.info(f"Connectors   : {CONNECTOR_IDS}")
    logger.info(f"Users        : {USER_IDS}")
    logger.info("=" * 72)

    provider: Optional[IGraphDBProvider] = None

    all_data: Dict[str, Dict[str, Any]] = {}
    for cid, label in zip(CONNECTOR_IDS, ("A", "B", "C")):
        all_data[cid] = _build_connector_data(cid, label)

    try:
        # -- Connect -----------------------------------------------------------
        logger.info(f"\n-- Connecting to {data_store} --")
        key_value_store = TestKeyValueStore(logger, "app/config/default_config.json")
        config_service = ConfigurationService(logger, key_value_store)
        provider = await GraphDBProviderFactory.create_provider(logger, config_service)

        qh = _create_query_helper(provider)

        # -- Test rounds -------------------------------------------------------
        test_cases = [
            ("TC-DEL-001: Delete Connector A -- B,C intact", CONN_A, [CONN_B, CONN_C]),
            ("TC-DEL-002: Delete Connector B -- A,C intact", CONN_B, [CONN_A, CONN_C]),
            ("TC-DEL-003: Delete Connector C -- A,B intact", CONN_C, [CONN_A, CONN_B]),
        ]

        for test_name, delete_id, survivor_ids in test_cases:
            logger.info(f"\n-- {test_name} --")

            logger.info("  Cleaning up previous connector data...")
            await cleanup_connector_data(provider, all_data, qh)

            logger.info("  Seeding all 3 connectors...")
            await seed_all_connectors(provider, all_data)

            await run_deletion_test(
                provider, qh, all_data, delete_id, survivor_ids, test_name
            )

    finally:
        # -- Cleanup -----------------------------------------------------------
        logger.info("\n-- Cleanup --")
        if provider is not None:
            try:
                qh = _create_query_helper(provider)
                await cleanup_connector_data(provider, all_data, qh)
                await cleanup_all(provider, all_data)
            except Exception as exc:
                logger.warning(f"Cleanup error (non-fatal): {exc}")
            try:
                await provider.disconnect()
                logger.info(f"{data_store} disconnected")
            except Exception as exc:
                logger.warning(f"Disconnect error (non-fatal): {exc}")

    # -- Summary ---------------------------------------------------------------
    passed = sum(1 for r in _results if r["status"] == PASS)
    skipped = sum(1 for r in _results if r["status"] == SKIP)
    failed = sum(1 for r in _results if r["status"] == FAIL)

    logger.info("")
    logger.info("=" * 72)
    logger.info("FULL TEST RESULTS")
    logger.info("=" * 72)
    for r in _results:
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[r["status"]]
        suffix = f"  --  {r['detail']}" if r["detail"] else ""
        logger.info(f"  {icon}  [{r['status']}]  {r['name']}{suffix}")
    logger.info("")
    logger.info("=" * 72)
    logger.info(
        f"Results:  {passed} passed  |  {skipped} skipped  |  {failed} failed"
        f"  (total {len(_results)})"
    )
    logger.info("=" * 72)

    if failed:
        logger.info("\nFailed tests -- details:")
        for r in _results:
            if r["status"] == FAIL:
                logger.info(f"\n  x  {r['name']}")
                logger.info(f"     Detail: {r['detail']}")


if __name__ == "__main__":
    asyncio.run(run_all())
