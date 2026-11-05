"""Stage states and ``ensure_nodes`` on PostgreSQL: the contract tests of the other backends (``test_stage_states_it``), run unchanged on one PG org.

The test functions are imported, so they are collected here with a ``provider`` fixture that is PostgreSQL. The code-graph contract tests
(``test_code_graph_real_backends``) write records through ``batch_upsert_records`` and join this module with the records part.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import pytest

from app.config.constants.arangodb import CollectionNames
from tests.integration.graph_db import test_stage_states_it as ss
from tests.integration.graph_db.postgres.pg_support import (
    OrgBound,
    StandInProvider,
    new_org,
)
from tests.integration.graph_db.test_stage_states_it import (  # noqa: F401
    test_a_compare_and_set_for_an_earlier_run_is_refused,
    test_a_null_field_clears_the_stored_value,
    test_a_stored_state_round_trips_through_the_model,
    test_concurrent_compare_and_sets_succeed_exactly_once,
    test_concurrent_creates_of_one_key_succeed_exactly_once,
    test_record_fields_are_written_only_on_the_current_revision,
    test_stale_scan_returns_the_oldest_first,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def bound(pg_db) -> OrgBound:
    return OrgBound(StandInProvider(logging.getLogger("pg-graph"), pg_db), new_org())


@pytest.fixture
def provider(bound) -> OrgBound:
    return bound


@pytest.fixture(autouse=True)
def _stage_records(monkeypatch: pytest.MonkeyPatch, bound) -> None:
    async def insert_record(provider, record_id, rev):  # noqa: ANN202
        await provider.batch_upsert_nodes([{"id": record_id, "orgId": ss.ORG, "contentRev": rev}], ss.RECORDS)
    monkeypatch.setattr(ss, "_insert_record", insert_record)
    # PostgreSQL reports the org of the row, the org the state was written under, not the document's field.
    monkeypatch.setattr(ss, "_org_of", lambda provider: provider.org)


async def test_ensure_nodes_creates_once_under_concurrency_and_never_modifies(bound: OrgBound) -> None:
    coll, key = CollectionNames.TOPICS.value, str(uuid.uuid4())
    await asyncio.gather(*(bound.ensure_nodes([{"id": key, "name": f"Writer {i}"}], coll) for i in range(50)))
    first = (await bound.get_document(key, coll))["name"]
    await bound.ensure_nodes([{"id": key, "name": "Changed"}, {"_key": "other", "name": "New"}], coll)
    assert (await bound.get_document(key, coll))["name"] == first
    assert (await bound.get_document("other", coll))["name"] == "New"
    with pytest.raises(ValueError):
        await bound.ensure_nodes([{"name": "no id"}], coll)


async def test_pipeline_schema_needs_nothing(bound: OrgBound) -> None:
    await bound.ensure_pipeline_schema()
    await bound.ensure_pipeline_schema()


async def test_stage_states_are_org_scoped(pg_db) -> None:
    a, b = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org()), OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    doc = ss._document(str(uuid.uuid4()))
    assert await a.stage_state_create(dict(doc))
    assert await b.stage_state_get(doc["id"]) is None
    assert await b.stage_state_create(dict(doc))  # the same key in another org is another state
