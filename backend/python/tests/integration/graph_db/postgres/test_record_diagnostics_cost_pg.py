"""The cost of ``write_attempt_start`` against ``update_node`` (see ``../test_record_diagnostics_cost.py``)."""
from __future__ import annotations

import pytest

from tests.integration.graph_db import record_diagnostics_cost_cases as cases
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


globals().update({name: getattr(cases, name) for name in cases.TEST_NAMES})
