"""
Neo4j Test Provider

Extended Neo4jProvider for integration tests with additional test-specific methods.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.arangodb import Connectors
from app.models.entities import AppMetadata, AppRole, Record
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

logger = logging.getLogger("test-graph-provider")


def _app_role_from_arango(doc: dict, connector_id: str) -> AppRole:
    """Build an :class:`AppRole` from a ``roles`` document (inverse of ``to_arango_base_role``)."""
    conn_name = doc.get("connectorName")
    try:
        app_name = Connectors(conn_name) if conn_name else Connectors.JIRA
    except ValueError:
        app_name = Connectors.JIRA
    return AppRole(
        id=str(doc.get("_key") or doc.get("id") or ""),
        org_id=doc.get("orgId", "") or "",
        app_name=app_name,
        connector_id=doc.get("connectorId", connector_id),
        source_role_id=doc.get("externalRoleId", "") or "",
        name=doc.get("name", "") or "",
        created_at=doc.get("createdAtTimestamp", 0) or 0,
        updated_at=doc.get("updatedAtTimestamp", 0) or 0,
        source_created_at=doc.get("sourceCreatedAtTimestamp"),
        source_updated_at=doc.get("sourceLastModifiedTimestamp"),
    )


class TestNeo4jProvider(Neo4jProvider):
    """
    Extended Neo4j provider for integration tests.
    
    Inherits all methods from Neo4jProvider (get_document, batch_upsert_records, etc.)
    and adds test-specific helper methods for assertions and queries.
    
    Usage:
        provider = TestNeo4jProvider(config_service)
        await provider.connect()
        
        # Use inherited methods
        doc = await provider.get_document("key", "collection")
        
        # Use test-specific methods
        count = await provider.count_records("connector-id")
        await provider.assert_min_records("connector-id", 5)
    """
    
    def __init__(
        self, 
        config_service: ConfigurationService,
        custom_logger: logging.Logger | None = None
    ) -> None:
        """
        Initialize test provider.
        
        Args:
            config_service: ConfigurationService instance
            custom_logger: Optional custom logger. Uses default if not provided.
        """
        super().__init__(
            logger=custom_logger or logger,
            config_service=config_service,
        )

    # =========================================================================
    # Test Helper Methods - Counts
    # =========================================================================
    
    async def count_records(self, connector_id: str, *, scoped: bool = False) -> int:
        """Return the number of Record nodes for a connector.

        With ``scoped=True`` only records with a live ``BELONGS_TO`` → RecordGroup edge are
        counted (not ``IS_OF_TYPE``): a full sync wipes and recreates sync edges
        (``delete_connector_sync_edges``) but leaves nodes and ``IS_OF_TYPE`` intact, so records
        for a project that left the filter scope keep ``IS_OF_TYPE`` yet lose ``BELONGS_TO``;
        scoped counting also excludes placeholder stubs. Default (``False``) counts every record
        for the connector — required by suites whose records have no RecordGroup (e.g. KB uploads).
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        if scoped:
            cypher = (
                "MATCH (r:Record {connectorId: $cid})-[:BELONGS_TO]->(:RecordGroup) "
                "RETURN count(DISTINCT r) AS c"
            )
        else:
            cypher = "MATCH (r:Record {connectorId: $cid}) RETURN count(DISTINCT r) AS c"
        result = await self.client.execute_query(cypher, {"cid": connector_id})
        return int(result[0]["c"]) if result else 0

    async def count_record_groups(self, connector_id: str, *, scoped: bool = False) -> int:
        """Return the number of RecordGroup nodes for a connector.

        With ``scoped=True`` only RecordGroups with a live ``BELONGS_TO`` → App edge are counted
        (same full-sync-wipe reasoning as :meth:`count_records`), so a project that left the filter
        scope, whose RecordGroup node still exists but lost its App edge, is not counted. Default
        counts every RecordGroup for the connector.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        if scoped:
            cypher = (
                "MATCH (g:RecordGroup {connectorId: $cid})-[:BELONGS_TO]->(:App) "
                "RETURN count(DISTINCT g) AS c"
            )
        else:
            cypher = "MATCH (g:RecordGroup {connectorId: $cid}) RETURN count(DISTINCT g) AS c"
        result = await self.client.execute_query(cypher, {"cid": connector_id})
        return int(result[0]["c"]) if result else 0

    async def count_user_groups(self, connector_id: str) -> int:
        """Count ``Group`` (Jira site user-group) nodes for this connector."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (g:Group {connectorId: $cid}) RETURN count(g) AS c",
            {"cid": connector_id},
        )
        return int(result[0]["c"]) if result else 0

    async def count_record_group_edges(self, connector_id: str) -> int:
        """Count Record -> RecordGroup BELONGS_TO edges."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (r:Record {connectorId: $cid})-[:BELONGS_TO]->(g:RecordGroup) RETURN count(*) AS c",
            {"cid": connector_id}
        )
        return int(result[0]["c"]) if result else 0

    async def count_group_hierarchy_edges(self, connector_id: str) -> int:
        """Count RecordGroup -> RecordGroup BELONGS_TO edges."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (child:RecordGroup {connectorId: $cid})-[:BELONGS_TO]->(parent:RecordGroup) RETURN count(*) AS c",
            {"cid": connector_id}
        )
        return int(result[0]["c"]) if result else 0

    async def count_parent_child_edges(self, connector_id: str) -> int:
        """Count parent/child folder edges (RECORD_RELATION with relationshipType PARENT_CHILD)."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (p {connectorId: $cid})-[r:RECORD_RELATION {relationshipType: 'PARENT_CHILD'}]->(c {connectorId: $cid})
            RETURN count(*) AS c
            """,
            {"cid": connector_id}
        )
        return int(result[0]["c"]) if result else 0

    async def count_permission_edges(self, connector_id: str) -> int:
        """Count permission-related edges touching Records for a connector."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        inherit_result = await self.client.execute_query(
            "MATCH (r:Record {connectorId: $cid})-[:INHERIT_PERMISSIONS]->() RETURN count(*) AS c",
            {"cid": connector_id}
        )
        direct_result = await self.client.execute_query(
            "MATCH ()-[:PERMISSION]->(r:Record {connectorId: $cid}) RETURN count(*) AS c",
            {"cid": connector_id}
        )
        inherit = int(inherit_result[0]["c"]) if inherit_result else 0
        direct = int(direct_result[0]["c"]) if direct_result else 0
        return inherit + direct

    async def count_user_to_group_permission_edges(
        self, connector_id: str, group_external_id: Optional[str] = None,
    ) -> int:
        """Count User -[:PERMISSION]-> Group for this connector's groups.

        When ``group_external_id`` is set, only edges to that Group
        (``externalGroupId``) are counted.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        if group_external_id is None:
            result = await self.client.execute_query(
                """
                MATCH (u:User)-[:PERMISSION]->(g:Group {connectorId: $cid})
                RETURN count(*) AS c
                """,
                {"cid": connector_id},
            )
        else:
            result = await self.client.execute_query(
                """
                MATCH (u:User)-[:PERMISSION]->(g:Group {connectorId: $cid, externalGroupId: $gid})
                RETURN count(*) AS c
                """,
                {"cid": connector_id, "gid": str(group_external_id)},
            )
        return int(result[0]["c"]) if result else 0

    async def count_user_to_role_permission_edges(
        self, connector_id: str, role_external_id: Optional[str] = None,
    ) -> int:
        """Count User -[:PERMISSION]-> Role (Jira project AppRole) for this connector.

        When ``role_external_id`` is set, only edges to that Role
        (``externalRoleId``) are counted.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        if role_external_id is None:
            result = await self.client.execute_query(
                """
                MATCH (u:User)-[:PERMISSION]->(role:Role {connectorId: $cid})
                RETURN count(*) AS c
                """,
                {"cid": connector_id},
            )
        else:
            result = await self.client.execute_query(
                """
                MATCH (u:User)-[:PERMISSION]->(role:Role {connectorId: $cid, externalRoleId: $rid})
                RETURN count(*) AS c
                """,
                {"cid": connector_id, "rid": str(role_external_id)},
            )
        return int(result[0]["c"]) if result else 0

    async def count_permission_edges_to_record_groups(
        self, connector_id: str, record_group_external_id: Optional[str] = None,
    ) -> int:
        """Count PERMISSION edges whose target is a RecordGroup (scheme / ACL holders).

        When ``record_group_external_id`` is set, only edges into that RecordGroup
        (``externalGroupId`` on the node — same id as :meth:`get_record_group_by_external_id`)
        are counted.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        if record_group_external_id is None:
            result = await self.client.execute_query(
                """
                MATCH ()-[e:PERMISSION]->(rg:RecordGroup {connectorId: $cid})
                RETURN count(e) AS c
                """,
                {"cid": connector_id},
            )
        else:
            result = await self.client.execute_query(
                """
                MATCH ()-[e:PERMISSION]->(rg:RecordGroup {connectorId: $cid, externalGroupId: $rgid})
                RETURN count(e) AS c
                """,
                {"cid": connector_id, "rgid": str(record_group_external_id)},
            )
        return int(result[0]["c"]) if result else 0

    async def count_app_record_group_edges(self, connector_id: str) -> int:
        """Count BELONGS_TO edges from RecordGroup to App for a connector."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (g:RecordGroup {connectorId: $cid})-[:BELONGS_TO]->(a:App) RETURN count(*) AS c",
            {"cid": connector_id}
        )
        return int(result[0]["c"]) if result else 0

    async def count_any_nodes_with_connector_id(self, connector_id: str) -> int:
        """Count any node that still carries this connectorId."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (n {connectorId: $cid}) RETURN count(n) AS c",
            {"cid": connector_id}
        )
        return int(result[0]["c"]) if result else 0

    # =========================================================================
    # Test Helper Methods - Record Lookups
    # =========================================================================

    async def fetch_record_paths(
        self, connector_id: str, limit: int = 200
    ) -> List[Tuple[Optional[str], Optional[str]]]:
        """Return up to *limit* (path, record_name) tuples."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
            RETURN f.path AS path, coalesce(r.recordName, r.name) AS record_name
            LIMIT $limit
            """,
            {"cid": connector_id, "limit": limit}
        )
        return [(rec.get("path"), rec.get("record_name")) for rec in result]

    async def fetch_record_names(self, connector_id: str) -> List[str]:
        """Return all record_name values for a connector."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            RETURN coalesce(r.recordName, r.name) AS name
            """,
            {"cid": connector_id}
        )
        return [rec["name"] for rec in result if rec.get("name")]

    async def get_record_by_name(
        self, connector_id: str, name: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a single Record by record_name. Returns None if not found."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            WHERE coalesce(r.recordName, r.name) = $name
            RETURN r AS record
            LIMIT 1
            """,
            {"cid": connector_id, "name": name}
        )
        return dict(result[0]["record"]) if result else None

    async def get_record_parent_group(
        self, connector_id: str, record_name: str
    ) -> Optional[str]:
        """Get the name of the RecordGroup that a Record BELONGS_TO."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            WHERE coalesce(r.recordName, r.name) = $name
            MATCH (r)-[:BELONGS_TO]->(g:RecordGroup)
            RETURN g.name AS group_name
            LIMIT 1
            """,
            {"cid": connector_id, "name": record_name}
        )
        return result[0]["group_name"] if result else None

    async def record_path_or_name_contains(
        self, connector_id: str, substring: str
    ) -> bool:
        """Return True if at least one Record has path or name containing substring."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
            WITH r, coalesce(r.recordName, r.name) AS name, f.path AS path
            WHERE (path IS NOT NULL AND path CONTAINS $sub) OR (name IS NOT NULL AND name CONTAINS $sub)
            RETURN count(*) AS c
            """,
            {"cid": connector_id, "sub": substring}
        )
        return int(result[0]["c"]) > 0 if result else False

    async def record_name_path_contains(
        self, connector_id: str, record_name: str, path_substring: str
    ) -> bool:
        """True if some Record named *record_name* has a File.path containing *path_substring*."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            WHERE coalesce(r.recordName, r.name) = $name
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
            WITH r, f
            WHERE f.path IS NOT NULL AND f.path CONTAINS $sub
            RETURN count(*) AS c
            """,
            {"cid": connector_id, "name": record_name, "sub": path_substring}
        )
        return int(result[0]["c"]) > 0 if result else False

    # =========================================================================
    # Test Helper Methods - Assertions
    # =========================================================================

    async def assert_min_records(self, connector_id: str, expected_min: int) -> None:
        """Assert that the connector has at least *expected_min* Record nodes."""
        actual = await self.count_records(connector_id)
        assert actual >= expected_min, (
            f"Expected at least {expected_min} Record nodes for connector {connector_id}, "
            f"found {actual}"
        )

    async def assert_record_groups_and_edges(
        self,
        connector_id: str,
        min_groups: int | None = None,
        min_record_edges: int | None = None,
    ) -> None:
        """Sanity checks on RecordGroup nodes and BELONGS_TO edges."""
        groups = await self.count_record_groups(connector_id)
        record_edges = await self.count_record_group_edges(connector_id)

        if min_groups is not None:
            assert groups >= min_groups, (
                f"Expected at least {min_groups} RecordGroup nodes, found {groups}"
            )
        if min_record_edges is not None:
            assert record_edges >= min_record_edges, (
                f"Expected at least {min_record_edges} Record->RecordGroup BELONGS_TO edges, "
                f"found {record_edges}"
            )

    async def assert_no_orphan_records(
        self, connector_id: str, max_orphans: int = 1
    ) -> None:
        """Assert every Record has at least one BELONGS_TO edge to a RecordGroup."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            WHERE NOT (r)-[:BELONGS_TO]->(:RecordGroup)
            RETURN count(r) AS c
            """,
            {"cid": connector_id}
        )
        orphans = int(result[0]["c"]) if result else 0
        if orphans > max_orphans:
            raise AssertionError(
                f"Found {orphans} orphan Record(s) with no BELONGS_TO edge for connector {connector_id} "
                f"(allowed up to {max_orphans})"
            )

    async def assert_app_record_group_edges(
        self, connector_id: str, min_edges: int = 1
    ) -> None:
        """Assert at least *min_edges* RecordGroup → App BELONGS_TO edges."""
        edges = await self.count_app_record_group_edges(connector_id)
        assert edges >= min_edges, (
            f"Expected at least {min_edges} RecordGroup→App BELONGS_TO edges for connector {connector_id}, "
            f"found {edges}"
        )

    async def assert_record_paths_or_names_contain(
        self, connector_id: str, expected_substrings: Iterable[str]
    ) -> None:
        """Assert that for each substring, at least one Record's path or name contains it."""
        for substring in expected_substrings:
            if not await self.record_path_or_name_contains(connector_id, substring):
                n = await self.count_records(connector_id)
                raise AssertionError(
                    f"No Record path or name for connector {connector_id} contained the expected "
                    f"substring '{substring}' (Record count: {n})"
                )

    async def assert_record_not_exists(self, connector_id: str, name: str) -> None:
        """Assert a record with this name does NOT exist."""
        rec = await self.get_record_by_name(connector_id, name)
        assert rec is None, (
            f"Record '{name}' should not exist for connector {connector_id}, but was found"
        )

    # =========================================================================
    # Test Helper Methods - Summaries
    # =========================================================================

    async def graph_summary(self, connector_id: str) -> Dict[str, int]:
        """Return a dict of graph stats for quick debugging."""
        return {
            "records": await self.count_records(connector_id),
            "record_groups": await self.count_record_groups(connector_id),
            "user_groups": await self.count_user_groups(connector_id),
            "belongs_to_edges": await self.count_record_group_edges(connector_id),
            "group_hierarchy_edges": await self.count_group_hierarchy_edges(connector_id),
            "parent_child_edges": await self.count_parent_child_edges(connector_id),
            "permission_edges": await self.count_permission_edges(connector_id),
            "permission_user_group_edges": await self.count_user_to_group_permission_edges(connector_id),
            "permission_user_role_edges": await self.count_user_to_role_permission_edges(connector_id),
            "permission_to_record_group_edges": await self.count_permission_edges_to_record_groups(connector_id),
            "app_record_group_edges": await self.count_app_record_group_edges(connector_id),
        }

    async def assert_connector_graph_fully_cleaned(
        self, connector_id: str, timeout: int = 180
    ) -> None:
        """
        Strict cleanup assertion after connector delete.
        Polls until every graph metric for this connector is zero.
        """
        poll_interval = 5
        deadline = time.time() + timeout
        last_summary: Dict[str, int] = {}

        while time.time() < deadline:
            last_summary = await self.graph_summary(connector_id)
            last_summary["any_nodes_with_connector_id"] = await self.count_any_nodes_with_connector_id(
                connector_id
            )
            if all(v == 0 for v in last_summary.values()):
                logger.info(
                    "Graph fully cleaned for connector %s (all counts zero).",
                    connector_id,
                )
                return
            await asyncio.sleep(poll_interval)

        raise AssertionError(
            f"Connector {connector_id} graph not fully cleaned after {timeout}s. "
            f"All counts must be zero after delete. Remaining: {last_summary}. "
            "Fix backend cleanup or increase timeout."
        )

    async def assert_all_records_cleaned(
        self, connector_id: str, timeout: int = 180
    ) -> None:
        """Alias for strict post-delete graph cleanup (same as graph_assertions)."""
        await self.assert_connector_graph_fully_cleaned(connector_id, timeout=timeout)

    async def assert_record_count_unchanged(
        self, connector_id: str, expected: int, tolerance: int = 0
    ) -> None:
        """Assert record count equals expected ± tolerance."""
        actual = await self.count_records(connector_id)
        assert abs(actual - expected) <= tolerance, (
            f"Expected ~{expected} records (±{tolerance}), found {actual}"
        )

    async def get_record_parent_path(
        self, connector_id: str, record_name: str
    ) -> Optional[str]:
        """Build folder path via BELONGS_TO (pure Cypher, no APOC required)."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            WHERE coalesce(r.recordName, r.name) = $name
            MATCH (r)-[:BELONGS_TO]->(g:RecordGroup)
            OPTIONAL MATCH path = (g)-[:BELONGS_TO*0..5]->(root:RecordGroup)
            WITH r, g, root, nodes(path) AS ns
            WITH r, [x IN ns | coalesce(x.name, '')] AS parts
            WITH r, reduce(s = '', p IN [p IN parts WHERE p <> ''] | 
                CASE WHEN s = '' THEN p ELSE s + '/' + p END
            ) AS parent_path
            RETURN parent_path
            LIMIT 1
            """,
            {"cid": connector_id, "name": record_name},
        )
        return result[0]["parent_path"] if result else None

    async def assert_record_paths_contain(
        self, connector_id: str, expected_substrings: Iterable[str]
    ) -> None:
        """Assert each substring appears in at least one Record File.path."""
        paths = [p for p, _ in await self.fetch_record_paths(connector_id, limit=500) if p]
        for substring in expected_substrings:
            if not any(substring in p for p in paths):
                raise AssertionError(
                    f"No Record.path for connector {connector_id} contained substring {substring!r}"
                )

    async def record_file_path_for_name(
        self, connector_id: str, record_name: str
    ) -> Optional[str]:
        """Return File.path for the Record with the given display name, if any."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            WHERE coalesce(r.recordName, r.name) = $name
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
            RETURN f.path AS path
            LIMIT 1
            """,
            {"cid": connector_id, "name": record_name},
        )
        if not result or result[0].get("path") is None:
            return None
        return str(result[0]["path"])

    async def record_paths_or_names_contain(
        self, connector_id: str, substrings: Iterable[str]
    ) -> bool:
        """True if each substring matches some Record path or name (wait-condition helper)."""
        for substring in substrings:
            if not await self.record_path_or_name_contains(connector_id, substring):
                return False
        return True

    async def assert_record_names_contain(
        self, connector_id: str, expected_names: Iterable[str]
    ) -> None:
        """Assert each expected name exists as a record_name."""
        names = set(await self.fetch_record_names(connector_id))
        for name in expected_names:
            assert name in names, (
                f"Expected record_name not found in graph for connector {connector_id} "
                f"(distinct names: {len(names)})."
            )

    # -------------------------------------------------------------------------
    # User / Organization (e2e user pipeline)
    # -------------------------------------------------------------------------

    async def graph_find_user_by_email(self, email: str) -> dict[str, Any] | None:
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (u:User) WHERE toLower(u.email) = toLower($email) RETURN u",
            {"email": email},
        )
        if not result:
            return None
        u = result[0]["u"]
        return dict(u) if u is not None else None

    async def graph_find_user_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (u:User {userId: $userId}) RETURN u",
            {"userId": user_id},
        )
        if not result:
            return None
        u = result[0]["u"]
        return dict(u) if u is not None else None

    async def graph_find_org(self, org_id: str) -> dict[str, Any] | None:
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (o:Organization {id: $orgId}) RETURN o",
            {"orgId": org_id},
        )
        if not result:
            return None
        o = result[0]["o"]
        return dict(o) if o is not None else None

    async def graph_org_exists(self, org_id: str) -> bool:
        return await self.graph_find_org(org_id) is not None
    
    # =========================================================================
    # Entity Lookup Methods (for assertions framework)
    # =========================================================================

    async def record_inherits_permissions(
        self, connector_id: str, external_record_id: str
    ) -> bool:
        """Check if record has INHERIT_PERMISSIONS edge."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid, externalRecordId: $eid})
            RETURN exists((r)-[:INHERIT_PERMISSIONS]->()) AS inherits
            """,
            {"cid": connector_id, "eid": external_record_id}
        )
        return bool(result[0]["inherits"]) if result else False
    
    async def count_group_members(
        self, connector_id: str, external_group_id: str
    ) -> int:
        """Count users in a group."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (g:Group {connectorId: $cid, externalGroupId: $gid})
            MATCH (u:User)-[:PERMISSION]->(g)
            RETURN count(u) AS count
            """,
            {"cid": connector_id, "gid": external_group_id}
        )
        return int(result[0]["count"]) if result else 0

    async def get_app_metadata_by_connector_id(
        self, connector_id: str
    ) -> Optional[AppMetadata]:
        """Load the connector app node as :class:`AppMetadata` (parity with Arango ``apps``)."""
        raw = await self.get_document(connector_id, CollectionNames.APPS.value)
        if not raw:
            return None
        doc = dict(raw)
        if "_key" not in doc and doc.get("id") is not None:
            doc["_key"] = str(doc["id"])
        return AppMetadata.from_db_document(doc)

    async def get_app_role_by_external_id(
        self, connector_id: str, role_external_id: str
    ) -> Optional[AppRole]:
        """Load a Jira project ``Role`` node (by ``externalRoleId``) as :class:`AppRole`."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (role:Role {connectorId: $cid, externalRoleId: $rid}) RETURN role LIMIT 1",
            {"cid": connector_id, "rid": str(role_external_id)},
        )
        if not result:
            return None
        doc = self._neo4j_to_arango_node(dict(result[0]["role"]), CollectionNames.ROLES.value)
        return _app_role_from_arango(doc, connector_id)

    async def fetch_records_by_type(
        self, connector_id: str, record_type: str
    ) -> List[Dict[str, Any]]:
        """Fetch records for a connector; ``record_type`` empty string means all types."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid})
            WHERE $rtype = '' OR r.recordType = $rtype
            RETURN r
            LIMIT 10000
            """,
            {"cid": connector_id, "rtype": record_type},
        )
        return [dict(r["r"]) for r in result] if result else []

    # =========================================================================
    # Edge-coverage helpers (Jira test plan)
    # =========================================================================

    async def count_records_by_type(self, connector_id: str, record_type: str, *, scoped: bool = False) -> int:
        """Count Record nodes filtered by recordType (e.g. TICKET, FILE).

        With ``scoped=True`` applies the same live ``BELONGS_TO`` → RecordGroup guard as
        :meth:`count_records`. Default counts every record of that type for the connector.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        if scoped:
            cypher = (
                "MATCH (r:Record {connectorId: $cid, recordType: $rtype})-[:BELONGS_TO]->(:RecordGroup) "
                "RETURN count(DISTINCT r) AS c"
            )
        else:
            cypher = (
                "MATCH (r:Record {connectorId: $cid, recordType: $rtype}) "
                "RETURN count(DISTINCT r) AS c"
            )
        result = await self.client.execute_query(cypher, {"cid": connector_id, "rtype": record_type})
        return int(result[0]["c"]) if result else 0

    async def count_inherit_permissions_edges(self, connector_id: str) -> int:
        """Aggregate count of INHERIT_PERMISSIONS edges (Record -> RecordGroup)."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (r:Record {connectorId: $cid})-[:INHERIT_PERMISSIONS]->() RETURN count(*) AS c",
            {"cid": connector_id},
        )
        return int(result[0]["c"]) if result else 0

    async def count_record_relation_edges(
        self, connector_id: str, relation_type: str
    ) -> int:
        """Count RECORD_RELATION edges of a specific relationshipType (PARENT_CHILD, ATTACHMENT, BLOCKS, etc.)."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (p {connectorId: $cid})-[r:RECORD_RELATION {relationshipType: $rtype}]->(c {connectorId: $cid})
            RETURN count(*) AS c
            """,
            {"cid": connector_id, "rtype": relation_type},
        )
        return int(result[0]["c"]) if result else 0

    async def count_entity_relations_edges(
        self, connector_id: str, edge_type: Optional[str] = None
    ) -> int:
        """Count ENTITYRELATIONS edges (Record -> User: LEAD_BY, ASSIGNED_TO, REPORTED_BY, CREATED_BY).

        When ``edge_type`` is None, counts all entity-relation edges originating from this connector's records.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        if edge_type is None:
            result = await self.client.execute_query(
                """
                MATCH (r:Record {connectorId: $cid})-[e:ENTITYRELATIONS]->()
                RETURN count(e) AS c
                """,
                {"cid": connector_id},
            )
        else:
            result = await self.client.execute_query(
                """
                MATCH (r:Record {connectorId: $cid})-[e:ENTITYRELATIONS {edgeType: $etype}]->()
                RETURN count(e) AS c
                """,
                {"cid": connector_id, "etype": edge_type},
            )
        return int(result[0]["c"]) if result else 0

    async def count_user_app_relation_edges(self, connector_id: str) -> int:
        """Count USER_APP_RELATION edges to the App associated with this connector.

        Visibility / log-only: USER_APP_RELATION is typically established at user/app
        registration, not by connector sync. Returns 0 if the App node has no users.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (g:RecordGroup {connectorId: $cid})-[:BELONGS_TO]->(app:App)
            WITH app LIMIT 1
            MATCH ()-[e:USER_APP_RELATION]->(app)
            RETURN count(e) AS c
            """,
            {"cid": connector_id},
        )
        return int(result[0]["c"]) if result else 0

    async def get_record_outgoing_relations(
        self, connector_id: str, external_record_id: str, relation_type: str
    ) -> List[str]:
        """Return external ids of records reachable via outbound RECORD_RELATION of the given relationshipType.

        For parent_child / attachment edges, the connector emits ``parent -> child``
        (see ``create_record_relation(parent_id, record_id, ...)``), so this method
        returns the **children** of the given record. To resolve the parent of a
        record, use :meth:`get_record_incoming_relations` or the
        ``parent_external_record_id`` field directly.
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        # No :Record label filter — placeholder records and tickets share the same
        # connectorId+externalRecordId index but may carry different labels.
        result = await self.client.execute_query(
            """
            MATCH (r {connectorId: $cid, externalRecordId: $eid})
            MATCH (r)-[:RECORD_RELATION {relationshipType: $rtype}]->(t)
            RETURN t.externalRecordId AS ext_id
            """,
            {"cid": connector_id, "eid": external_record_id, "rtype": relation_type},
        )
        return [rec["ext_id"] for rec in result if rec.get("ext_id")] if result else []

    async def get_record_incoming_relations(
        self, connector_id: str, external_record_id: str, relation_type: str
    ) -> List[str]:
        """Return external ids of records pointing TO this record via RECORD_RELATION of the given type.

        Inverse of :meth:`get_record_outgoing_relations`. For parent_child / attachment
        edges, this returns the parent (since the connector stores the edge
        ``parent -> child``).
        """
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r {connectorId: $cid, externalRecordId: $eid})
            MATCH (src)-[:RECORD_RELATION {relationshipType: $rtype}]->(r)
            RETURN src.externalRecordId AS ext_id
            """,
            {"cid": connector_id, "eid": external_record_id, "rtype": relation_type},
        )
        return [rec["ext_id"] for rec in result if rec.get("ext_id")] if result else []

    async def get_record_outgoing_entity_relations(
        self, connector_id: str, external_record_id: str, edge_type: str
    ) -> List[str]:
        """Return user ids on the receiving end of ENTITYRELATIONS edges with the given edgeType."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid, externalRecordId: $eid})
            MATCH (r)-[:ENTITYRELATIONS {edgeType: $etype}]->(u)
            RETURN coalesce(u.sourceUserId, u.userId, u.id) AS uid
            """,
            {"cid": connector_id, "eid": external_record_id, "etype": edge_type},
        )
        return [rec["uid"] for rec in result if rec.get("uid")] if result else []

    async def get_record_parent_external_id(
        self, connector_id: str, external_record_id: str
    ) -> Optional[str]:
        """Return the ``parentExternalRecordId`` field of a record, or None if unset."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid, externalRecordId: $eid})
            RETURN r.parentExternalRecordId AS pid
            LIMIT 1
            """,
            {"cid": connector_id, "eid": external_record_id},
        )
        if not result:
            return None
        pid = result[0].get("pid")
        return str(pid) if pid is not None else None

    async def get_typed_record_by_external_id(
        self, connector_id: str, external_record_id: str
    ) -> Optional[Record]:
        """Load base :Record plus ``IS_OF_TYPE`` document as a typed ``TicketRecord`` / ``FileRecord`` / …"""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid, externalRecordId: $eid})
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(typeDoc)
            RETURN r, typeDoc
            LIMIT 1
            """,
            {"cid": connector_id, "eid": external_record_id},
        )
        if not result:
            return None
        row = result[0]
        record_dict = dict(row["r"])
        record_dict = self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value)
        type_doc = dict(row["typeDoc"]) if row.get("typeDoc") else None
        if type_doc:
            type_doc = self._neo4j_to_arango_node(type_doc, "")
        try:
            return self._create_typed_record_from_neo4j(record_dict, type_doc)
        except ValueError:
            return Record.from_arango_base_record(record_dict)

    _ARANGO_TO_NEO4J_EDGE: dict[str, str] = {
        "recordRelations": "RECORD_RELATION",
        "belongsTo": "BELONGS_TO",
        "inheritPermissions": "INHERIT_PERMISSIONS",
        "permission": "PERMISSION",
        "isOfType": "IS_OF_TYPE",
        "entityRelations": "ENTITYRELATIONS",
        "userAppRelation": "USER_APP_RELATION",
    }

    _ARANGO_COLLECTION_TO_NEO4J_LABEL: dict[str, str] = {
        "users": "User",
        "groups": "Group",
        "roles": "Role",
        "organizations": "Organization",
        "records": "Record",
        "recordGroups": "RecordGroup",
        "apps": "App",
        "tickets": "Ticket",
        "files": "File",
        "mails": "Mail",
        "webpages": "Webpage",
        "comments": "Comment",
        "links": "Link",
        "projects": "Project",
        "products": "Product",
        "deals": "Deal",
        "meetings": "Meeting",
        "artifacts": "Artifact",
        "codeFiles": "CodeFile",
        "prs": "PullRequest",
        "sqlTables": "SqlTable",
        "sqlViews": "SqlView",
    }

    async def find_edges_between(
        self,
        from_collection: str,
        from_key: str,
        to_collection: str,
        to_key: str,
        edge_collection: str,
    ) -> List[Dict[str, Any]]:
        """Return raw edge properties between two specific vertices."""
        if not self.client:
            raise RuntimeError("Provider not connected")

        from_label = self._ARANGO_COLLECTION_TO_NEO4J_LABEL.get(from_collection)
        to_label = self._ARANGO_COLLECTION_TO_NEO4J_LABEL.get(to_collection)
        edge_label = self._ARANGO_TO_NEO4J_EDGE.get(edge_collection)

        if not from_label or not to_label or not edge_label:
            logger.warning(
                "find_edges_between: unmapped collection/edge: "
                "from=%s, to=%s, edge=%s",
                from_collection, to_collection, edge_collection,
            )
            return []

        query = (
            f"MATCH (a:{from_label} {{id: $fk}})"
            f"-[r:{edge_label}]->"
            f"(b:{to_label} {{id: $tk}}) "
            f"RETURN properties(r) AS props"
        )
        result = await self.client.execute_query(
            query, {"fk": from_key, "tk": to_key},
        )
        return [dict(row["props"]) for row in result] if result else []

