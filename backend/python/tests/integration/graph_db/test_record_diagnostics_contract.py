"""The contract script on ArangoDB and Neo4j (PostgreSQL: ``postgres/test_record_diagnostics_contract_pg.py``): the contract script
removes the four fields, resumes after a stop, and leaves every record updatable under the older schema."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db import record_diagnostics_contract_cases as cases
from tests.integration.graph_db.record_diagnostics_support import Store, new_org
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
logger = logging.getLogger("record-diagnostics-contract-it")


@pytest.fixture(params=["neo4j", "arango"])
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Store]:
    try:
        provider = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "record_diagnostics_contract_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    made = Store(request.param, provider, new_org())
    try:
        yield made
    finally:
        await made.cleanup()
        await provider.disconnect()


# Neo4j stores no null, so the control has nothing to leave behind there: only ArangoDB is a parameter, and the Neo4j job never selects it.
@pytest.mark.parametrize("store", ["arango"], indirect=True)
async def test_a_script_that_skips_stored_nulls_leaves_keys_behind(store: Store) -> None:
    await cases.skipped_nulls_body(store)


globals().update({name: getattr(cases, name) for name in cases.TEST_NAMES})
