"""The contract script on PostgreSQL: the same cases as ``../test_record_diagnostics_contract.py``, with JSON nulls among the keys."""
from __future__ import annotations

import pytest

from tests.integration.graph_db import record_diagnostics_contract_cases as cases
from tests.integration.graph_db.record_diagnostics_support import Store, new_org

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


@pytest.fixture
async def store(pg_db, pg_provider, org_ctx, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    org = new_org()
    made = Store("postgres", pg_provider, org, raw=pg_db)
    yield made
    await made.cleanup()
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_a_script_that_skips_stored_nulls_leaves_keys_behind(store: Store) -> None:
    await cases.skipped_nulls_body(store)


globals().update({name: getattr(cases, name) for name in cases.TEST_NAMES})
