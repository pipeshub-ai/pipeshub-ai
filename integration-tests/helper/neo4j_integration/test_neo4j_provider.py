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
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

logger = logging.getLogger("test-graph-provider")


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
    
    async def count_records(self, connector_id: str) -> int:
        """Return the number of Record nodes for a connector."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (r:Record {connectorId: $cid}) RETURN count(r) AS c",
            {"cid": connector_id}
        )
        return int(result[0]["c"]) if result else 0

    async def count_record_groups(self, connector_id: str) -> int:
        """Return the number of RecordGroup nodes for a connector."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            "MATCH (g:RecordGroup {connectorId: $cid}) RETURN count(g) AS c",
            {"cid": connector_id}
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
            "belongs_to_edges": await self.count_record_group_edges(connector_id),
            "group_hierarchy_edges": await self.count_group_hierarchy_edges(connector_id),
            "parent_child_edges": await self.count_parent_child_edges(connector_id),
            "permission_edges": await self.count_permission_edges(connector_id),
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
    
    async def fetch_records_by_type(
        self, connector_id: str, record_type: str
    ) -> List[Dict[str, Any]]:
        """Fetch all records of a specific type."""
        if not self.client:
            raise RuntimeError("Provider not connected")
        result = await self.client.execute_query(
            """
            MATCH (r:Record {connectorId: $cid, recordType: $rtype})
            RETURN r
            LIMIT 10000
            """,
            {"cid": connector_id, "rtype": record_type}
        )
        return [dict(r["r"]) for r in result] if result else []
