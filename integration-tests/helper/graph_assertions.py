"""
Graph assertion helpers for integration tests.

All queries run against the remote Neo4j Aura instance. The graph model uses:
  - Record nodes          (files)
  - RecordGroup nodes     (buckets / folders)
  - BELONGS_TO edges      Record → RecordGroup, RecordGroup → RecordGroup
  - RECORD_RELATION edges with relationshipType "PARENT_CHILD" (folder hierarchy)
  - PERMISSION / INHERIT_PERMISSIONS edges for access (not a literal HAS_PERMISSION type)
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from neo4j import Driver
import logging

logger = logging.getLogger("graph-assertions")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _run_single_int(driver: Driver, cypher: str, **params: object) -> int:
    with driver.session() as session:
        result = session.run(cypher, **params)
        rec = result.single()
        return int(rec["c"]) if rec else 0


def _run_query(driver: Driver, cypher: str, **params: object) -> List[Dict[str, Any]]:
    with driver.session() as session:
        result = session.run(cypher, **params)
        return [dict(record) for record in result]


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def count_records(driver: Driver, connector_id: str) -> int:
    """Return the number of Record nodes for a connector."""
    return _run_single_int(
        driver,
        "MATCH (r:Record {connectorId: $cid}) RETURN count(r) AS c",
        cid=connector_id,
    )


def count_record_groups(driver: Driver, connector_id: str) -> int:
    """Return the number of RecordGroup nodes for a connector."""
    return _run_single_int(
        driver,
        "MATCH (g:RecordGroup {connectorId: $cid}) RETURN count(g) AS c",
        cid=connector_id,
    )


def count_record_group_edges(driver: Driver, connector_id: str) -> int:
    """Count Record -> RecordGroup BELONGS_TO edges."""
    return _run_single_int(
        driver,
        "MATCH (r:Record {connectorId: $cid})-[:BELONGS_TO]->(g:RecordGroup) RETURN count(*) AS c",
        cid=connector_id,
    )


def count_group_hierarchy_edges(driver: Driver, connector_id: str) -> int:
    """Count RecordGroup -> RecordGroup BELONGS_TO edges."""
    return _run_single_int(
        driver,
        "MATCH (child:RecordGroup {connectorId: $cid})-[:BELONGS_TO]->(parent:RecordGroup) RETURN count(*) AS c",
        cid=connector_id,
    )


def count_parent_child_edges(driver: Driver, connector_id: str) -> int:
    """Count parent/child folder edges (RECORD_RELATION with relationshipType PARENT_CHILD)."""
    return _run_single_int(
        driver,
        """
        MATCH (p {connectorId: $cid})-[r:RECORD_RELATION {relationshipType: 'PARENT_CHILD'}]->(c {connectorId: $cid})
        RETURN count(*) AS c
        """,
        cid=connector_id,
    )


def count_permission_edges(driver: Driver, connector_id: str) -> int:
    """Count permission-related edges touching Records for a connector (inherit + direct PERMISSION)."""
    inherit = _run_single_int(
        driver,
        "MATCH (r:Record {connectorId: $cid})-[:INHERIT_PERMISSIONS]->() RETURN count(*) AS c",
        cid=connector_id,
    )
    direct = _run_single_int(
        driver,
        "MATCH ()-[:PERMISSION]->(r:Record {connectorId: $cid}) RETURN count(*) AS c",
        cid=connector_id,
    )
    return inherit + direct


def count_app_record_group_edges(driver: Driver, connector_id: str) -> int:
    """Count BELONGS_TO edges from RecordGroup to App for a connector."""
    return _run_single_int(
        driver,
        "MATCH (g:RecordGroup {connectorId: $cid})-[:BELONGS_TO]->(a:App) RETURN count(*) AS c",
        cid=connector_id,
    )


# ---------------------------------------------------------------------------
# Record lookups
# ---------------------------------------------------------------------------

def fetch_record_paths(
    driver: Driver, connector_id: str, limit: int = 200
) -> List[Tuple[str, str]]:
    """Return up to *limit* (path, record_name) tuples. Path comes from File node when present."""
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
    RETURN f.path AS path, coalesce(r.recordName, r.name) AS record_name
    LIMIT $limit
    """
    with driver.session() as session:
        result = session.run(cypher, cid=connector_id, limit=limit)
        return [(rec["path"], rec["record_name"]) for rec in result]


def fetch_record_names(driver: Driver, connector_id: str) -> List[str]:
    """Return all record_name values for a connector (recordName / name in Neo4j)."""
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    RETURN coalesce(r.recordName, r.name) AS name
    """
    with driver.session() as session:
        result = session.run(cypher, cid=connector_id)
        return [rec["name"] for rec in result]


def get_record_by_name(
    driver: Driver, connector_id: str, name: str
) -> Optional[Dict[str, Any]]:
    """Retrieve a single Record by record_name. Returns None if not found."""
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    WHERE coalesce(r.recordName, r.name) = $name
    RETURN r AS record
    LIMIT 1
    """
    with driver.session() as session:
        result = session.run(cypher, cid=connector_id, name=name)
        rec = result.single()
        return dict(rec["record"]) if rec else None


def get_record_parent_group(
    driver: Driver, connector_id: str, record_name: str
) -> Optional[str]:
    """Get the name of the RecordGroup that a Record BELONGS_TO."""
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    WHERE coalesce(r.recordName, r.name) = $name
    MATCH (r)-[:BELONGS_TO]->(g:RecordGroup)
    RETURN g.name AS group_name
    LIMIT 1
    """
    with driver.session() as session:
        result = session.run(cypher, cid=connector_id, name=record_name)
        rec = result.single()
        return rec["group_name"] if rec else None


def get_record_parent_path(
    driver: Driver, connector_id: str, record_name: str
) -> Optional[str]:
    """
    Traverse BELONGS_TO edges from a Record up through RecordGroups to build its path.

    Returns a string like "bucket/folder/subfolder" or None if no parent groups exist.
    """
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    WHERE coalesce(r.recordName, r.name) = $name
    MATCH (r)-[:BELONGS_TO]->(g:RecordGroup)
    OPTIONAL MATCH path = (g)-[:BELONGS_TO*0..5]->(root:RecordGroup)
    WITH r, g, root, nodes(path) AS ns
    WITH r, [x IN ns | coalesce(x.name, '')] AS parts
    RETURN apoc.text.join([p IN parts WHERE p <> '' | p], '/') AS parent_path
    LIMIT 1
    """
    with driver.session() as session:
        result = session.run(cypher, cid=connector_id, name=record_name)
        rec = result.single()
        return rec["parent_path"] if rec else None


# ---------------------------------------------------------------------------
# Assertions - basic
# ---------------------------------------------------------------------------

def assert_min_records(driver: Driver, connector_id: str, expected_min: int) -> None:
    """Assert that the connector has at least *expected_min* Record nodes."""
    actual = count_records(driver, connector_id)
    assert actual >= expected_min, (
        f"Expected at least {expected_min} Record nodes for connector {connector_id}, "
        f"found {actual}"
    )


def assert_record_groups_and_edges(
    driver: Driver,
    connector_id: str,
    min_groups: int | None = None,
    min_record_edges: int | None = None,
) -> None:
    """Sanity checks on RecordGroup nodes and BELONGS_TO edges."""
    groups = count_record_groups(driver, connector_id)
    record_edges = count_record_group_edges(driver, connector_id)

    if min_groups is not None:
        assert groups >= min_groups, (
            f"Expected at least {min_groups} RecordGroup nodes, found {groups}"
        )
    if min_record_edges is not None:
        assert record_edges >= min_record_edges, (
            f"Expected at least {min_record_edges} Record->RecordGroup BELONGS_TO edges, "
            f"found {record_edges}"
        )


# ---------------------------------------------------------------------------
# Assertions - record names
# ---------------------------------------------------------------------------

def assert_record_paths_contain(
    driver: Driver,
    connector_id: str,
    expected_substrings: Iterable[str],
) -> None:
    """Assert that for each expected substring, at least one Record path contains it."""
    paths = [p for p, _ in fetch_record_paths(driver, connector_id, limit=500)]
    for substring in expected_substrings:
        if not any(substring in p for p in paths):
            raise AssertionError(
                f"No Record.path for connector {connector_id} contained substring {substring!r}"
            )


def record_path_or_name_contains(
    driver: Driver, connector_id: str, substring: str
) -> bool:
    """
    Return True if at least one Record for this connector has path or name containing
    *substring*. Uses a single Cypher query with no limit, so safe for large graphs.

    Useful as a wait condition (e.g. after rename/move) before asserting.
    """
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
    WITH r, coalesce(r.recordName, r.name) AS name, f.path AS path
    WHERE (path IS NOT NULL AND path CONTAINS $sub) OR (name IS NOT NULL AND name CONTAINS $sub)
    RETURN count(*) AS c
    """
    with driver.session() as session:
        result = session.run(cypher, cid=connector_id, sub=substring)
        rec = result.single()
        return int(rec["c"]) > 0 if rec else False


def record_file_path_for_name(
    driver: Driver, connector_id: str, record_name: str
) -> Optional[str]:
    """Return File.path for the Record with the given display name, if any."""
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    WHERE coalesce(r.recordName, r.name) = $name
    OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
    RETURN f.path AS path
    LIMIT 1
    """
    with driver.session() as session:
        result = session.run(cypher, cid=connector_id, name=record_name)
        rec = result.single()
        if not rec or rec["path"] is None:
            return None
        return str(rec["path"])


def record_name_path_contains(
    driver: Driver,
    connector_id: str,
    record_name: str,
    path_substring: str,
) -> bool:
    """
    True if some Record named *record_name* has a File.path containing *path_substring*.

    Use after object-store moves: bucket/container/share maps to one RecordGroup; prefixes
    are path segments, not extra RecordGroups.

    When the same basename exists on multiple paths (e.g. Azure Files copy+delete move
    before the old path is deleted from the graph), we must not use LIMIT 1 on name
    alone — that can return the stale path and miss the move.
    """
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    WHERE coalesce(r.recordName, r.name) = $name
    OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
    WITH r, f
    WHERE f.path IS NOT NULL AND f.path CONTAINS $sub
    RETURN count(*) AS c
    """
    with driver.session() as session:
        result = session.run(
            cypher, cid=connector_id, name=record_name, sub=path_substring
        )
        rec = result.single()
        return int(rec["c"]) > 0 if rec else False


def record_paths_or_names_contain(
    driver: Driver,
    connector_id: str,
    substrings: Iterable[str],
) -> bool:
    """
    Return True if for each substring, at least one Record's path or name contains it.

    Useful as a wait condition (e.g. after rename) before asserting.
    """
    for substring in substrings:
        if not record_path_or_name_contains(driver, connector_id, substring):
            return False
    return True


def assert_record_paths_or_names_contain(
    driver: Driver,
    connector_id: str,
    expected_substrings: Iterable[str],
) -> None:
    """
    Assert that for each expected substring, at least one Record's path or name contains it.

    Uses the same count-based query as record_path_or_name_contains so it stays consistent
    with the wait condition and works when the graph has many records (no limit).
    """
    for substring in expected_substrings:
        if not record_path_or_name_contains(driver, connector_id, substring):
            n = count_records(driver, connector_id)
            raise AssertionError(
                f"No Record path or name for connector {connector_id} contained the expected "
                f"substring (Record count: {n}). Graph paths/names are omitted from this message."
            )


def assert_record_names_contain(
    driver: Driver,
    connector_id: str,
    expected_names: Iterable[str],
) -> None:
    """Assert that each expected name exists as a record_name."""
    names = set(fetch_record_names(driver, connector_id))
    for name in expected_names:
        assert name in names, (
            f"Expected record_name not found in graph for connector {connector_id} "
            f"(distinct names: {len(names)}). Name lists are omitted from this message."
        )


def assert_record_not_exists(
    driver: Driver, connector_id: str, name: str
) -> None:
    """Assert a record with this name does NOT exist."""
    rec = get_record_by_name(driver, connector_id, name)
    assert rec is None, (
        f"Record '{name}' should not exist for connector {connector_id}, but was found"
    )


# ---------------------------------------------------------------------------
# Assertions - edges and hierarchy
# ---------------------------------------------------------------------------

def assert_no_orphan_records(
    driver: Driver, connector_id: str, max_orphans: int = 1
) -> None:
    """
    Assert every Record has at least one BELONGS_TO edge to a RecordGroup.

    Some connectors may legitimately create a small number of special-case
    records (e.g. roots) without BELONGS_TO. We allow a small tolerance via
    max_orphans to keep tests resilient to those implementation details.
    """
    cypher = """
    MATCH (r:Record {connectorId: $cid})
    WHERE NOT (r)-[:BELONGS_TO]->(:RecordGroup)
    RETURN count(r) AS c
    """
    orphans = _run_single_int(driver, cypher, cid=connector_id)
    if orphans > max_orphans:
        raise AssertionError(
            f"Found {orphans} orphan Record(s) with no BELONGS_TO edge for connector {connector_id} "
            f"(allowed up to {max_orphans})"
        )


def count_any_nodes_with_connector_id(driver: Driver, connector_id: str) -> int:
    """
    Count any node that still carries this connectorId (Record, RecordGroup, etc.).
    Used to catch leftover nodes after delete that edge-only counts might miss.
    """
    return _run_single_int(
        driver,
        "MATCH (n {connectorId: $cid}) RETURN count(n) AS c",
        cid=connector_id,
    )


def assert_connector_graph_fully_cleaned(
    driver: Driver, connector_id: str, timeout: int = 180
) -> None:
    """
    Strict cleanup assertion after connector delete.

    Polls until every graph metric for this connector is zero, or fails with
    a full snapshot. No silent pass — residual nodes/edges mean cleanup failed.
    A persistent failure here indicates the backend is not removing graph data
    when a connector is deleted; fix connector-delete cleanup, not the test.

    Args:
        driver: Neo4j driver.
        connector_id: Connector instance id.
        timeout: Seconds to wait for full cleanup (default 180).
    """
    import time

    poll_interval = 5
    deadline = time.time() + timeout
    last_summary: Dict[str, int] = {}

    while time.time() < deadline:
        last_summary = graph_summary(driver, connector_id)
        last_summary["any_nodes_with_connector_id"] = count_any_nodes_with_connector_id(
            driver, connector_id
        )
        if all(v == 0 for v in last_summary.values()):
            logger.info(
                "Graph fully cleaned for connector %s (all counts zero).",
                connector_id,
            )
            return
        time.sleep(poll_interval)

    # Fail with full detail — tests must not pass if graph still has this connector
    raise AssertionError(
        f"Connector {connector_id} graph not fully cleaned after {timeout}s. "
        f"All counts must be zero after delete. Remaining: {last_summary}. "
        "Fix backend cleanup or increase timeout; do not ignore residual graph data."
    )


def assert_all_records_cleaned(
    driver: Driver, connector_id: str, timeout: int = 180
) -> None:
    """
    Assert zero Records, RecordGroups, and all counted edge types for a connector.

    Delegates to assert_connector_graph_fully_cleaned (strict — fails if anything remains).
    """
    assert_connector_graph_fully_cleaned(driver, connector_id, timeout=timeout)


def assert_record_count_unchanged(
    driver: Driver, connector_id: str, expected: int, tolerance: int = 0
) -> None:
    """Assert record count equals expected ± tolerance."""
    actual = count_records(driver, connector_id)
    assert abs(actual - expected) <= tolerance, (
        f"Expected ~{expected} records (±{tolerance}), found {actual}"
    )


# ---------------------------------------------------------------------------
# Full graph snapshot (for debugging)
# ---------------------------------------------------------------------------

def graph_summary(driver: Driver, connector_id: str) -> Dict[str, int]:
    """Return a dict of graph stats for quick debugging."""
    return {
        "records": count_records(driver, connector_id),
        "record_groups": count_record_groups(driver, connector_id),
        "belongs_to_edges": count_record_group_edges(driver, connector_id),
        "group_hierarchy_edges": count_group_hierarchy_edges(driver, connector_id),
        "parent_child_edges": count_parent_child_edges(driver, connector_id),
        "permission_edges": count_permission_edges(driver, connector_id),
        "app_record_group_edges": count_app_record_group_edges(driver, connector_id),
    }


def assert_app_record_group_edges(
    driver: Driver, connector_id: str, min_edges: int = 1
) -> None:
    """
    Assert there are at least *min_edges* RecordGroup → App BELONGS_TO edges for this connector.

    This verifies that top-level record groups are connected to the App node as expected by
    the record_group_app_edge_migration.
    """
    edges = count_app_record_group_edges(driver, connector_id)
    assert edges >= min_edges, (
        f"Expected at least {min_edges} RecordGroup→App BELONGS_TO edges for connector {connector_id}, "
        f"found {edges}"
    )
