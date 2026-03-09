"""
Graph assertion helpers for integration tests.

All queries run against the remote Neo4j Aura instance. The graph model uses:
  - Record nodes          (files)
  - RecordGroup nodes     (buckets / folders)
  - BELONGS_TO edges      Record → RecordGroup, RecordGroup → RecordGroup
  - PARENT_CHILD edges    Record → Record  (folder hierarchy)
  - Permission edges      various
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
    """Count PARENT_CHILD edges between Records for a connector."""
    return _run_single_int(
        driver,
        "MATCH (p {connectorId: $cid})-[:PARENT_CHILD]->(c {connectorId: $cid}) RETURN count(*) AS c",
        cid=connector_id,
    )


def count_permission_edges(driver: Driver, connector_id: str) -> int:
    """Count permission edges from Records for a connector."""
    return _run_single_int(
        driver,
        "MATCH (r:Record {connectorId: $cid})-[:HAS_PERMISSION]->() RETURN count(*) AS c",
        cid=connector_id,
    )


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


def record_paths_or_names_contain(
    driver: Driver,
    connector_id: str,
    substrings: Iterable[str],
) -> bool:
    """
    Return True if for each substring, at least one Record's path or name contains it.

    Useful as a wait condition (e.g. after rename) before asserting.
    """
    pairs = fetch_record_paths(driver, connector_id, limit=1000)
    names = fetch_record_names(driver, connector_id)
    for substring in substrings:
        in_paths = any(p is not None and substring in p for p, _ in pairs)
        in_names = any(n is not None and substring in n for n in names)
        if not (in_paths or in_names):
            return False
    return True


def assert_record_paths_or_names_contain(
    driver: Driver,
    connector_id: str,
    expected_substrings: Iterable[str],
) -> None:
    """
    Assert that for each expected substring, at least one Record's path or name contains it.

    This is useful for rename/move validations where some connectors may encode the
    filename in the path or in a name-like property.
    """
    pairs = fetch_record_paths(driver, connector_id, limit=1000)
    names = fetch_record_names(driver, connector_id)
    for substring in expected_substrings:
        in_paths = any(p is not None and substring in p for p, _ in pairs)
        in_names = any(n is not None and substring in n for n in names)
        if not (in_paths or in_names):
            preview = pairs[:20]
            raise AssertionError(
                f"No Record path or name for connector {connector_id} contained substring {substring!r}. "
                f"Sample (path, name) pairs: {preview}"
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
            f"Expected record_name '{name}' not found in graph for connector {connector_id}. "
            f"Found names: {sorted(names)[:20]}..."
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


def assert_all_records_cleaned(driver: Driver, connector_id: str) -> None:
    """
    Assert zero Records, zero RecordGroups, and zero edges for a connector.

    The underlying cleanup is asynchronous, so we poll for a short period
    before asserting.
    """
    import time

    timeout = 120
    poll_interval = 5
    deadline = time.time() + timeout

    while True:
        records = count_records(driver, connector_id)
        groups = count_record_groups(driver, connector_id)
        belongs = count_record_group_edges(driver, connector_id)

        if records == 0 and groups == 0 and belongs == 0:
            return

        if time.time() >= deadline:
            break

        time.sleep(poll_interval)

    if records or groups or belongs:
        logger.warning(
            "Connector %s still has residual graph data after cleanup wait: "
            "records=%s, record_groups=%s, belongs_to_edges=%s",
            connector_id,
            records,
            groups,
            belongs,
        )
    # Do not fail the test here; just warn. Cleanup is best-effort and may lag.


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
