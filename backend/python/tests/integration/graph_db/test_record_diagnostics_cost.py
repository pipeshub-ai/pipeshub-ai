"""The store cost of the diagnostics writes on ArangoDB and Neo4j (PostgreSQL in
``postgres/test_record_diagnostics_cost_pg.py``). The save-transaction duration is in ``test_record_diagnostics_stall_bound_arango.py``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db import record_diagnostics_cost_cases as cases
from tests.integration.graph_db.record_diagnostics_support import Store, new_org
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
logger = logging.getLogger("record-diagnostics-cost-it")


@pytest.fixture(params=["neo4j", "arango"])
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Store]:
    try:
        provider = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "record_diagnostics_cost_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    made = Store(request.param, provider, new_org())
    try:
        yield made
    finally:
        await made.cleanup()
        await provider.disconnect()


globals().update({name: getattr(cases, name) for name in cases.TEST_NAMES})
