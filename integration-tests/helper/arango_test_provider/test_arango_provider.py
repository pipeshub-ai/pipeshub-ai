"""
ArangoDB Test Provider

Extended ArangoHTTPProvider for integration tests with additional test-specific methods using AQL.
Collection and edge names come from backend ``CollectionNames`` (single source of truth).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config.constants.arangodb import CollectionNames
from app.config.configuration_service import ConfigurationService
from app.models.entities import AppMetadata, Record
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

logger = logging.getLogger("test-graph-provider")


class TestArangoHTTPProvider(ArangoHTTPProvider):
    """
    Extended ArangoDB HTTP provider for integration tests.

    Inherits all methods from ArangoHTTPProvider (get_document, batch_upsert_records, etc.)
    and adds test-specific helper methods for assertions and queries using AQL.

    Usage:
        provider = TestArangoHTTPProvider(config_service)
        await provider.connect()

        count = await provider.count_records("connector-id")
        await provider.assert_min_records("connector-id", 5)
    """

    def __init__(
        self, 
        config_service: ConfigurationService,
        custom_logger: logging.Logger | None = None
    ) -> None:
        super().__init__(logger=custom_logger or logger, config_service=config_service)

    # connect() / disconnect() inherited: env-based connect when ``config_service`` is None (see backend).

    # =========================================================================
    # Test Helper Methods - Counts
    # =========================================================================

    async def count_records(self, connector_id: str) -> int:
        """Return the number of Record documents for a connector."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"FOR r IN {CollectionNames.RECORDS.value} FILTER r.connectorId == @cid RETURN 1"
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_record_groups(self, connector_id: str) -> int:
        """Return the number of RecordGroup documents for a connector."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"FOR g IN {CollectionNames.RECORD_GROUPS.value} FILTER g.connectorId == @cid RETURN 1"
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_user_groups(self, connector_id: str) -> int:
        """Count user-group documents for this connector (Jira site ``groups`` collection)."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"FOR g IN {CollectionNames.GROUPS.value} FILTER g.connectorId == @cid RETURN 1"
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_record_group_edges(self, connector_id: str) -> int:
        """Count Record -> RecordGroup BELONGS_TO edges."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FOR v, e IN OUTBOUND r {CollectionNames.BELONGS_TO.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', v)
                    RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_group_hierarchy_edges(self, connector_id: str) -> int:
        """Count RecordGroup -> RecordGroup BELONGS_TO edges."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR g IN {CollectionNames.RECORD_GROUPS.value}
                FILTER g.connectorId == @cid
                FOR v, e IN OUTBOUND g {CollectionNames.BELONGS_TO.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', v)
                    RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_parent_child_edges(self, connector_id: str) -> int:
        """Count parent/child folder edges (RECORD_RELATION with relationshipType PARENT_CHILD)."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR e IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER e.relationshipType == 'PARENT_CHILD'
                LET from_doc = DOCUMENT(e._from)
                LET to_doc = DOCUMENT(e._to)
                FILTER from_doc.connectorId == @cid AND to_doc.connectorId == @cid
                RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_permission_edges(self, connector_id: str) -> int:
        """Count permission-related edges touching Records for a connector."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")

        inherit_query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FOR v, e IN OUTBOUND r {CollectionNames.INHERIT_PERMISSIONS.value}
                    RETURN 1
        """
        inherit_result = await self.http_client.execute_aql(inherit_query, {"cid": connector_id})
        inherit = len(inherit_result) if inherit_result else 0

        direct_query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FOR v, e IN INBOUND r {CollectionNames.PERMISSION.value}
                    RETURN 1
        """
        direct_result = await self.http_client.execute_aql(direct_query, {"cid": connector_id})
        direct = len(direct_result) if direct_result else 0

        return inherit + direct

    async def count_user_to_group_permission_edges(
        self, connector_id: str, group_external_id: Optional[str] = None,
    ) -> int:
        """Count users linked to this connector's groups via PERMISSION (membership).

        If ``group_external_id`` is set, only PERMISSION edges to that Group
        document (``externalGroupId``) are counted.
        """
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        if group_external_id is None:
            query = f"""
                FOR e IN {CollectionNames.PERMISSION.value}
                    LET to_doc = DOCUMENT(e._to)
                    LET from_doc = DOCUMENT(e._from)
                    FILTER to_doc != null AND from_doc != null
                    FILTER to_doc.connectorId == @cid
                    FILTER IS_SAME_COLLECTION('{CollectionNames.GROUPS.value}', to_doc)
                    FILTER IS_SAME_COLLECTION('{CollectionNames.USERS.value}', from_doc)
                    RETURN 1
            """
            bind_vars: Dict[str, Any] = {"cid": connector_id}
        else:
            query = f"""
                FOR e IN {CollectionNames.PERMISSION.value}
                    LET to_doc = DOCUMENT(e._to)
                    LET from_doc = DOCUMENT(e._from)
                    FILTER to_doc != null AND from_doc != null
                    FILTER to_doc.connectorId == @cid
                    FILTER to_doc.externalGroupId == @gid
                    FILTER IS_SAME_COLLECTION('{CollectionNames.GROUPS.value}', to_doc)
                    FILTER IS_SAME_COLLECTION('{CollectionNames.USERS.value}', from_doc)
                    RETURN 1
            """
            bind_vars = {"cid": connector_id, "gid": str(group_external_id)}
        result = await self.http_client.execute_aql(query, bind_vars)
        return len(result) if result else 0

    async def count_user_to_role_permission_edges(
        self, connector_id: str, role_external_id: Optional[str] = None,
    ) -> int:
        """Count users linked to this connector's Jira project roles via PERMISSION.

        If ``role_external_id`` is set, only PERMISSION edges to that Role
        document (``externalRoleId``) are counted.
        """
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        if role_external_id is None:
            query = f"""
                FOR e IN {CollectionNames.PERMISSION.value}
                    LET to_doc = DOCUMENT(e._to)
                    LET from_doc = DOCUMENT(e._from)
                    FILTER to_doc != null AND from_doc != null
                    FILTER to_doc.connectorId == @cid
                    FILTER IS_SAME_COLLECTION('{CollectionNames.ROLES.value}', to_doc)
                    FILTER IS_SAME_COLLECTION('{CollectionNames.USERS.value}', from_doc)
                    RETURN 1
            """
            bind_vars: Dict[str, Any] = {"cid": connector_id}
        else:
            query = f"""
                FOR e IN {CollectionNames.PERMISSION.value}
                    LET to_doc = DOCUMENT(e._to)
                    LET from_doc = DOCUMENT(e._from)
                    FILTER to_doc != null AND from_doc != null
                    FILTER to_doc.connectorId == @cid
                    FILTER to_doc.externalRoleId == @rid
                    FILTER IS_SAME_COLLECTION('{CollectionNames.ROLES.value}', to_doc)
                    FILTER IS_SAME_COLLECTION('{CollectionNames.USERS.value}', from_doc)
                    RETURN 1
            """
            bind_vars = {"cid": connector_id, "rid": str(role_external_id)}
        result = await self.http_client.execute_aql(query, bind_vars)
        return len(result) if result else 0

    async def count_permission_edges_to_record_groups(
        self, connector_id: str, record_group_external_id: Optional[str] = None,
    ) -> int:
        """Count PERMISSION edges whose head is a RecordGroup (browse / scheme holders).

        If ``record_group_external_id`` is set, only edges targeting that
        RecordGroup (``externalGroupId``) are counted.
        """
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        if record_group_external_id is None:
            query = f"""
                FOR e IN {CollectionNames.PERMISSION.value}
                    LET to_doc = DOCUMENT(e._to)
                    FILTER to_doc != null
                    FILTER to_doc.connectorId == @cid
                    FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', to_doc)
                    RETURN 1
            """
            bind_vars: Dict[str, Any] = {"cid": connector_id}
        else:
            query = f"""
                FOR e IN {CollectionNames.PERMISSION.value}
                    LET to_doc = DOCUMENT(e._to)
                    FILTER to_doc != null
                    FILTER to_doc.connectorId == @cid
                    FILTER to_doc.externalGroupId == @rgid
                    FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', to_doc)
                    RETURN 1
            """
            bind_vars = {"cid": connector_id, "rgid": str(record_group_external_id)}
        result = await self.http_client.execute_aql(query, bind_vars)
        return len(result) if result else 0

    async def count_app_record_group_edges(self, connector_id: str) -> int:
        """Count BELONGS_TO edges from RecordGroup to App for a connector."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR g IN {CollectionNames.RECORD_GROUPS.value}
                FILTER g.connectorId == @cid
                FOR v, e IN OUTBOUND g {CollectionNames.BELONGS_TO.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.APPS.value}', v)
                    RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_any_nodes_with_connector_id(self, connector_id: str) -> int:
        """Count any document that still carries this connectorId."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")

        total = 0
        for coll in (
            CollectionNames.RECORDS,
            CollectionNames.RECORD_GROUPS,
            CollectionNames.FILES,
            CollectionNames.LINKS,
            CollectionNames.MAILS,
            CollectionNames.WEBPAGES,
            CollectionNames.COMMENTS,
            CollectionNames.PEOPLE,
            CollectionNames.USERS,
            CollectionNames.GROUPS,
            CollectionNames.ROLES,
            CollectionNames.ORGS,
            CollectionNames.TICKETS,
            CollectionNames.MEETINGS,
            CollectionNames.PROJECTS,
            CollectionNames.DEALS,
            CollectionNames.PRODUCTS,
        ):
            name = coll.value
            query = f"FOR doc IN {name} FILTER doc.connectorId == @cid RETURN 1"
            try:
                result = await self.http_client.execute_aql(query, {"cid": connector_id})
                total += len(result) if result else 0
            except Exception as e:
                # Skip collections that do not exist in this deployment; surface real failures.
                msg = str(e).lower()
                if any(
                    s in msg
                    for s in (
                        "404",
                        "unknown collection",
                        "collection or view not found",
                    )
                ):
                    logger.debug(
                        "Skipping connectorId count for collection %s (not available): %s",
                        name,
                        e,
                    )
                    continue
                logger.warning(
                    "Unexpected error counting connectorId in collection %s: %s",
                    name,
                    e,
                )
                raise

        return total

    # =========================================================================
    # Test Helper Methods - Record Lookups
    # =========================================================================

    async def fetch_record_paths(
        self, connector_id: str, limit: int = 200
    ) -> List[Tuple[Optional[str], Optional[str]]]:
        """Return up to *limit* (path, record_name) tuples."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                LET file_path = (
                    FOR v, e IN OUTBOUND r {CollectionNames.IS_OF_TYPE.value}
                        FILTER IS_SAME_COLLECTION('{CollectionNames.FILES.value}', v)
                        LIMIT 1
                        RETURN v.path
                )[0]
                LIMIT @limit
                RETURN {{
                    path: file_path,
                    record_name: NOT_NULL(r.recordName, r.name)
                }}
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id, "limit": limit})
        return [(rec.get("path"), rec.get("record_name")) for rec in result] if result else []

    async def fetch_record_names(self, connector_id: str) -> List[str]:
        """Return all record_name values for a connector."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                RETURN NOT_NULL(r.recordName, r.name)
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return [name for name in result if name] if result else []

    async def get_record_by_name(
        self, connector_id: str, name: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a single Record by record_name. Returns None if not found."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FILTER NOT_NULL(r.recordName, r.name) == @name
                LIMIT 1
                RETURN r
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id, "name": name})
        return result[0] if result else None

    async def get_record_parent_group(
        self, connector_id: str, record_name: str
    ) -> Optional[str]:
        """Get the name of the RecordGroup that a Record BELONGS_TO."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FILTER NOT_NULL(r.recordName, r.name) == @name
                FOR g, e IN OUTBOUND r {CollectionNames.BELONGS_TO.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', g)
                    LIMIT 1
                    RETURN g.name
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id, "name": record_name})
        return result[0] if result else None

    async def record_path_or_name_contains(
        self, connector_id: str, substring: str
    ) -> bool:
        """Return True if at least one Record has path or name containing substring."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                LET name = NOT_NULL(r.recordName, r.name)
                LET file_path = (
                    FOR v, e IN OUTBOUND r {CollectionNames.IS_OF_TYPE.value}
                        FILTER IS_SAME_COLLECTION('{CollectionNames.FILES.value}', v)
                        LIMIT 1
                        RETURN v.path
                )[0]
                FILTER (file_path != null AND CONTAINS(file_path, @sub)) OR (name != null AND CONTAINS(name, @sub))
                LIMIT 1
                RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id, "sub": substring})
        return len(result) > 0 if result else False

    async def record_name_path_contains(
        self, connector_id: str, record_name: str, path_substring: str
    ) -> bool:
        """True if some Record named *record_name* has a File.path containing *path_substring*."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FILTER NOT_NULL(r.recordName, r.name) == @name
                FOR v, e IN OUTBOUND r {CollectionNames.IS_OF_TYPE.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.FILES.value}', v)
                    FILTER v.path != null AND CONTAINS(v.path, @sub)
                    LIMIT 1
                    RETURN 1
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "name": record_name, "sub": path_substring}
        )
        return len(result) > 0 if result else False

    # =========================================================================
    # Test Helper Methods - Assertions
    # =========================================================================

    async def assert_min_records(self, connector_id: str, expected_min: int) -> None:
        """Assert that the connector has at least *expected_min* Record documents."""
        actual = await self.count_records(connector_id)
        assert actual >= expected_min, (
            f"Expected at least {expected_min} Record documents for connector {connector_id}, "
            f"found {actual}"
        )

    async def assert_record_groups_and_edges(
        self,
        connector_id: str,
        min_groups: int | None = None,
        min_record_edges: int | None = None,
    ) -> None:
        """Sanity checks on RecordGroup documents and BELONGS_TO edges."""
        groups = await self.count_record_groups(connector_id)
        record_edges = await self.count_record_group_edges(connector_id)

        if min_groups is not None:
            assert groups >= min_groups, (
                f"Expected at least {min_groups} RecordGroup documents, found {groups}"
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
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                LET has_belongs_to = LENGTH(
                    FOR v, e IN OUTBOUND r {CollectionNames.BELONGS_TO.value}
                        FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', v)
                        LIMIT 1
                        RETURN 1
                ) > 0
                FILTER !has_belongs_to
                RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        orphans = len(result) if result else 0
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
        """Alias for strict post-delete graph cleanup."""
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
        """Build folder path via BELONGS_TO traversal."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FILTER NOT_NULL(r.recordName, r.name) == @name
                FOR g, e IN OUTBOUND r {CollectionNames.BELONGS_TO.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', g)
                    LIMIT 1
                    LET path_parts = (
                        FOR v, e, p IN 0..5 OUTBOUND g {CollectionNames.BELONGS_TO.value}
                            FILTER IS_SAME_COLLECTION('{CollectionNames.RECORD_GROUPS.value}', v)
                            RETURN NOT_NULL(v.name, '')
                    )
                    LET filtered_parts = (FOR part IN path_parts FILTER part != '' RETURN part)
                    RETURN LENGTH(filtered_parts) > 0 ? CONCAT_SEPARATOR('/', filtered_parts) : null
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id, "name": record_name})
        return result[0] if result and result[0] else None

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
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FILTER NOT_NULL(r.recordName, r.name) == @name
                FOR v, e IN OUTBOUND r {CollectionNames.IS_OF_TYPE.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.FILES.value}', v)
                    LIMIT 1
                    RETURN v.path
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id, "name": record_name})
        return str(result[0]) if result and result[0] else None

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
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR u IN {CollectionNames.USERS.value}
                FILTER LOWER(u.email) == LOWER(@email)
                LIMIT 1
                RETURN u
        """
        result = await self.http_client.execute_aql(query, {"email": email})
        return result[0] if result else None

    async def graph_find_user_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR u IN {CollectionNames.USERS.value}
                FILTER u.userId == @userId
                LIMIT 1
                RETURN u
        """
        result = await self.http_client.execute_aql(query, {"userId": user_id})
        return result[0] if result else None

    async def graph_find_org(self, org_id: str) -> dict[str, Any] | None:
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR o IN {CollectionNames.ORGS.value}
                FILTER o.id == @orgId
                LIMIT 1
                RETURN o
        """
        result = await self.http_client.execute_aql(query, {"orgId": org_id})
        return result[0] if result else None

    async def graph_org_exists(self, org_id: str) -> bool:
        return await self.graph_find_org(org_id) is not None

    # =========================================================================
    # Protocol-declared helpers (parity with Neo4j)
    # =========================================================================

    async def record_inherits_permissions(
        self, connector_id: str, external_record_id: str
    ) -> bool:
        """Check if record has an INHERIT_PERMISSIONS edge to its RecordGroup."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid AND r.externalRecordId == @eid
                LIMIT 1
                LET edges = (
                    FOR v, e IN OUTBOUND r {CollectionNames.INHERIT_PERMISSIONS.value}
                        LIMIT 1
                        RETURN 1
                )
                RETURN LENGTH(edges) > 0
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "eid": external_record_id}
        )
        return bool(result[0]) if result else False

    async def get_app_metadata_by_connector_id(
        self, connector_id: str
    ) -> Optional[AppMetadata]:
        """Load the connector app document (``apps`` collection) as :class:`AppMetadata`."""
        raw = await self.get_document(connector_id, CollectionNames.APPS.value)
        if not raw:
            return None
        doc = dict(raw)
        if "_key" not in doc and doc.get("id") is not None:
            doc["_key"] = str(doc["id"])
        return AppMetadata.from_db_document(doc)

    async def count_group_members(
        self, connector_id: str, external_group_id: str
    ) -> int:
        """Count users connected to a group via PERMISSION edges (the connector's group-membership edge)."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR g IN {CollectionNames.GROUPS.value}
                FILTER g.connectorId == @cid AND g.externalGroupId == @gid
                LIMIT 1
                FOR v, e IN INBOUND g {CollectionNames.PERMISSION.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.USERS.value}', v)
                    RETURN 1
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "gid": external_group_id}
        )
        return len(result) if result else 0

    async def fetch_records_by_type(
        self, connector_id: str, record_type: str
    ) -> List[Dict[str, Any]]:
        """Fetch records for a connector; ``record_type`` empty string means all types."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FILTER @rtype == '' OR r.recordType == @rtype
                LIMIT 10000
                RETURN r
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "rtype": record_type}
        )
        return list(result) if result else []

    # =========================================================================
    # Edge-coverage helpers (Jira test plan)
    # =========================================================================

    async def count_records_by_type(self, connector_id: str, record_type: str) -> int:
        """Count Record documents filtered by recordType."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid AND r.recordType == @rtype
                RETURN 1
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "rtype": record_type}
        )
        return len(result) if result else 0

    async def count_inherit_permissions_edges(self, connector_id: str) -> int:
        """Aggregate count of INHERIT_PERMISSIONS edges from this connector's records."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid
                FOR v, e IN OUTBOUND r {CollectionNames.INHERIT_PERMISSIONS.value}
                    RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def count_record_relation_edges(
        self, connector_id: str, relation_type: str
    ) -> int:
        """Count RECORD_RELATIONS edges with a specific relationshipType."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR e IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER e.relationshipType == @rtype
                LET from_doc = DOCUMENT(e._from)
                LET to_doc = DOCUMENT(e._to)
                FILTER from_doc.connectorId == @cid AND to_doc.connectorId == @cid
                RETURN 1
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "rtype": relation_type}
        )
        return len(result) if result else 0

    async def count_entity_relations_edges(
        self, connector_id: str, edge_type: Optional[str] = None
    ) -> int:
        """Count ENTITY_RELATIONS edges (Record -> User: LEAD_BY, ASSIGNED_TO, etc.).

        When ``edge_type`` is None, counts edges of any edgeType originating from this
        connector's records.
        """
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        if edge_type is None:
            query = f"""
                FOR e IN {CollectionNames.ENTITY_RELATIONS.value}
                    LET from_doc = DOCUMENT(e._from)
                    FILTER from_doc.connectorId == @cid
                    RETURN 1
            """
            result = await self.http_client.execute_aql(query, {"cid": connector_id})
        else:
            query = f"""
                FOR e IN {CollectionNames.ENTITY_RELATIONS.value}
                    FILTER e.edgeType == @etype
                    LET from_doc = DOCUMENT(e._from)
                    FILTER from_doc.connectorId == @cid
                    RETURN 1
            """
            result = await self.http_client.execute_aql(
                query, {"cid": connector_id, "etype": edge_type}
            )
        return len(result) if result else 0

    async def count_user_app_relation_edges(self, connector_id: str) -> int:
        """Count USER_APP_RELATION edges to the App linked to this connector.

        Log-only: this edge is typically created at user/app registration, not by
        connector sync. Returns 0 if no users are registered against the app.
        """
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR g IN {CollectionNames.RECORD_GROUPS.value}
                FILTER g.connectorId == @cid
                FOR v, e IN OUTBOUND g {CollectionNames.BELONGS_TO.value}
                    FILTER IS_SAME_COLLECTION('{CollectionNames.APPS.value}', v)
                    LIMIT 1
                    FOR u, ue IN INBOUND v {CollectionNames.USER_APP_RELATION.value}
                        RETURN 1
        """
        result = await self.http_client.execute_aql(query, {"cid": connector_id})
        return len(result) if result else 0

    async def get_record_outgoing_relations(
        self, connector_id: str, external_record_id: str, relation_type: str
    ) -> List[str]:
        """Return externalRecordIds of records reachable via outbound RECORD_RELATIONS of the given type.

        For parent_child / attachment edges, the connector emits ``parent -> child``
        so this returns the **children**. To resolve the parent, use
        :meth:`get_record_incoming_relations` or the ``parentExternalRecordId`` field.
        """
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid AND r.externalRecordId == @eid
                LIMIT 1
                FOR v, e IN OUTBOUND r {CollectionNames.RECORD_RELATIONS.value}
                    FILTER e.relationshipType == @rtype
                    RETURN v.externalRecordId
        """
        result = await self.http_client.execute_aql(
            query,
            {"cid": connector_id, "eid": external_record_id, "rtype": relation_type},
        )
        return [str(x) for x in result if x] if result else []

    async def get_record_incoming_relations(
        self, connector_id: str, external_record_id: str, relation_type: str
    ) -> List[str]:
        """Return externalRecordIds of records pointing TO this record via RECORD_RELATIONS of the given type.

        Inverse of :meth:`get_record_outgoing_relations`. For parent_child / attachment
        edges, this returns the parent (since the connector stores the edge
        ``parent -> child``).
        """
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid AND r.externalRecordId == @eid
                LIMIT 1
                FOR v, e IN INBOUND r {CollectionNames.RECORD_RELATIONS.value}
                    FILTER e.relationshipType == @rtype
                    RETURN v.externalRecordId
        """
        result = await self.http_client.execute_aql(
            query,
            {"cid": connector_id, "eid": external_record_id, "rtype": relation_type},
        )
        return [str(x) for x in result if x] if result else []

    async def get_record_outgoing_entity_relations(
        self, connector_id: str, external_record_id: str, edge_type: str
    ) -> List[str]:
        """Return user ids on the receiving end of ENTITY_RELATIONS edges of the given edgeType."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid AND r.externalRecordId == @eid
                LIMIT 1
                FOR v, e IN OUTBOUND r {CollectionNames.ENTITY_RELATIONS.value}
                    FILTER e.edgeType == @etype
                    RETURN NOT_NULL(v.sourceUserId, NOT_NULL(v.userId, v._key))
        """
        result = await self.http_client.execute_aql(
            query,
            {"cid": connector_id, "eid": external_record_id, "etype": edge_type},
        )
        return [str(x) for x in result if x] if result else []

    async def get_record_parent_external_id(
        self, connector_id: str, external_record_id: str
    ) -> Optional[str]:
        """Return the ``parentExternalRecordId`` field of a record (or None)."""
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid AND r.externalRecordId == @eid
                LIMIT 1
                RETURN r.parentExternalRecordId
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "eid": external_record_id}
        )
        if not result:
            return None
        val = result[0]
        return str(val) if val else None

    async def get_typed_record_by_external_id(
        self, connector_id: str, external_record_id: str
    ) -> Optional[Record]:
        if not self.http_client:
            raise RuntimeError("Provider not connected")
        query = f"""
            FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorId == @cid AND r.externalRecordId == @eid
                LIMIT 1
                LET typeDoc = FIRST(
                    FOR v IN OUTBOUND r {CollectionNames.IS_OF_TYPE.value}
                        LIMIT 1
                        RETURN v
                )
                RETURN {{ record: r, typeDoc: typeDoc }}
        """
        result = await self.http_client.execute_aql(
            query, {"cid": connector_id, "eid": external_record_id}
        )
        if not result or not result[0]:
            return None
        row = result[0]
        record_dict = row.get("record") or {}
        type_doc = row.get("typeDoc")
        try:
            return self._create_typed_record_from_arango(record_dict, type_doc)
        except ValueError:
            rd = self._translate_node_from_arango(record_dict)
            return Record.from_arango_base_record(rd)

