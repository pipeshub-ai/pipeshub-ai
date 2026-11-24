"""The indexing flows on ArangoDB and Neo4j (PostgreSQL: ``postgres/test_record_diagnostics_flows_pg.py``).

The flows F1-F22 through the production write paths (see ``record_diagnostics_flows_support``), each compared with
the table of expected values; a flow whose rule is broken must fail (the negative controls); an older process's
validator keeps working on records that carry the fields; and the attempt counter is incremented in the statement.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db import record_diagnostics_flows_cases as cases
from tests.integration.graph_db.record_diagnostics_support import Store, new_org
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
logger = logging.getLogger("record-diagnostics-flows-it")


@pytest.fixture(params=["neo4j", "arango"])
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Store]:
    try:
        provider = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "record_diagnostics_flows_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    made = Store(request.param, provider, new_org())
    try:
        yield made
    finally:
        await made.cleanup()
        await provider.disconnect()



# one body per case, run here on the store the ``store`` fixture names
# ArangoDB checks in the server (its old schema is the narrow-schema test in the re-widen file): the validator in front of the
# store is the provider's, on Neo4j. Only Neo4j is a parameter here, so the ArangoDB job never selects it.
@pytest.mark.parametrize("store", ["neo4j"], indirect=True)
async def test_old_validator(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    await cases.old_validator_body(store, monkeypatch)


globals().update({name: getattr(cases, name) for name in cases.TEST_NAMES})
