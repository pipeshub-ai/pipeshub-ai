"""Which Neo4j notifications reach the log.

A query that reads a property key, label or relationship type the database has never stored carries an UNRECOGNIZED
notification, which the driver logs as a warning with the whole query on every run. Graphs that do not use a feature
yet (most of them, for most optional properties) would log one per listing query, so that classification is filtered.
Every other one, a deprecation among them, still reaches the log.
"""

from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.integration


def _notes(caplog: pytest.LogCaptureFixture, text: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "neo4j.notifications" and text in r.getMessage()]


async def test_a_never_stored_name_is_not_reported(neo4j_provider, caplog) -> None:
    caplog.set_level(logging.INFO, logger="neo4j.notifications")
    await neo4j_provider.client.execute_query(
        "MATCH (n:NotificationProbeNeverStored)-[:NOTIFICATION_PROBE_NEVER_STORED]->(m) "
        "WHERE n.notificationProbeNeverStored = 1 RETURN count(m) AS c")
    assert _notes(caplog, "does not exist") == []


async def test_a_deprecation_is_still_reported(neo4j_provider, caplog) -> None:
    caplog.set_level(logging.INFO, logger="neo4j.notifications")
    # An importing WITH in a CALL subquery is deprecated in Neo4j 5.x for CALL (x) { ... }.
    await neo4j_provider.client.execute_query("WITH 1 AS x CALL { WITH x RETURN x AS y } RETURN y")
    assert _notes(caplog, "deprecated")
