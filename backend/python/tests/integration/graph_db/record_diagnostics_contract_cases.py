"""The test bodies of the contract script, shared by the ArangoDB/Neo4j file and the PostgreSQL file."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any

from app.core.org_context import OrgContext, bind
from app.scripts import record_diagnostics_contract as script
from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    use_pre_diagnostics_validator,
)

if TYPE_CHECKING:
    import pytest

FIELDS = {"firstQueuedAt": 1_700_000_000_000, "failedAt": 1_700_000_100_000, "indexingAttempts": 2, "pageCount": 12}


async def _seed(store: Store, n: int = 7) -> list[str]:
    keys = []
    for i in range(n):
        key = store.key("k")
        # record 1 carries only stored nulls, the others a mix of values
        await store.put(key, **(FIELDS if i % 2 == 0 else {} if i == 1 else {"firstQueuedAt": 5}))
        keys.append(key)
    # a stored null is a key too (ArangoDB keeps it; PostgreSQL keeps a JSON null; Neo4j has none)
    if store.name == "arango":
        await store.raw_provider.http_client.update_document(RECORDS, keys[1], {"pageCount": None, "failedAt": None})
    elif store.name == "postgres":
        with bind(OrgContext(store.org, None, "job", "job:test", "t")):
            await store._pg_db.run(lambda tx: tx.execute(
                "UPDATE graph.nodes SET doc = doc || '{\"pageCount\": null, \"failedAt\": null}'::jsonb WHERE org_id = $1 AND id = $2", store.org, keys[1]), write=True)
    return keys


async def _call(store: Store, fn: Any, *a: Any, **k: Any) -> Any:  # noqa: ANN401
    if store.name == "postgres":
        with bind(OrgContext(store.org, None, "job", "job:test", "t")):
            return await fn(*a, **k)
    return await fn(*a, **k)


async def test_the_script_removes_all_four_keys_and_every_record_then_updates_under_the_older_schema(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = await _seed(store)
    graph = store.raw_provider
    # record 1 holds only stored nulls, which Neo4j cannot store
    assert await _call(store, script.check, graph, store.org) == len(keys) - (store.name == "neo4j")

    out = io.StringIO()
    left = await _call(store, script.apply, graph, store.org, batch_size=3, out=out)

    assert left == 0
    assert [json.loads(line) for line in out.getvalue().splitlines()][-1] == {"left": 0}
    for key in keys:
        doc = await store.doc(key)
        assert not set(FIELDS) & {name for name, value in doc.items() if value is not None or name in FIELDS}, (key, doc)
    await _update_every_record_under_the_older_schema(store, keys, monkeypatch)


async def _update_every_record_under_the_older_schema(store: Store, keys: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    if store.name == "arango":
        from tests.integration.graph_db.record_diagnostics_support import narrow_schema

        assert await store.raw_provider.http_client.update_collection_schema(RECORDS, narrow_schema())
        try:
            for key in keys:
                assert await store.p.update_node(key, RECORDS, {"indexingStatus": "QUEUED"})
        finally:
            from app.schema.arango.documents import record_schema

            await store.raw_provider.http_client.update_collection_schema(RECORDS, record_schema)
    else:
        use_pre_diagnostics_validator(monkeypatch)
        for key in keys:
            assert await store.p.update_node(key, RECORDS, {"indexingStatus": "QUEUED"})
            assert await store.p.update_node_if_match(key, RECORDS, {**(await store.doc(key)), "id": key, "_key": key}, "version", 1) in (True, False)


async def test_the_script_can_be_stopped_and_run_again(store: Store) -> None:
    keys = await _seed(store, 6)
    graph = store.raw_provider
    removed = await _call(store, script._STORES[script._store_of(graph)], graph, store.org, 2)  # a run killed after one batch
    assert removed == 2
    assert 0 < await _call(store, script.check, graph, store.org) < len(keys)

    assert await _call(store, script.apply, graph, store.org, batch_size=2, out=io.StringIO()) == 0
    assert await _call(store, script.apply, graph, store.org, batch_size=2, out=io.StringIO()) == 0  # idempotent


async def skipped_nulls_body(store: Store) -> None:
    """The negative control: removal that only looks at non-null values (PostgreSQL's `doc->>'k' IS NOT NULL`, and the
    equivalent on the other stores) leaves a stored null, which an older schema refuses on ArangoDB."""
    keys = await _seed(store, 3)
    if store.name == "arango":
        await store.raw_provider.http_client.execute_aql(
            "FOR r IN @@c FILTER r.orgId == @org AND (r.firstQueuedAt != null OR r.failedAt != null OR r.pageCount != null OR r.indexingAttempts != null) "
            "UPDATE r WITH { firstQueuedAt: null, failedAt: null, indexingAttempts: null, pageCount: null } IN @@c OPTIONS { keepNull: false }",
            {"@c": RECORDS, "org": store.org})
    else:
        with bind(OrgContext(store.org, None, "job", "job:test", "t")):
            await store._pg_db.run(lambda tx: tx.execute(
                "UPDATE graph.nodes SET doc = doc - ARRAY['firstQueuedAt','failedAt','indexingAttempts','pageCount'] "
                "WHERE org_id = $1 AND coll = 'records' AND (doc->>'firstQueuedAt' IS NOT NULL OR doc->>'failedAt' IS NOT NULL "
                "OR doc->>'indexingAttempts' IS NOT NULL OR doc->>'pageCount' IS NOT NULL)", store.org), write=True)

    assert await _call(store, script.check, store.raw_provider, store.org) > 0, f"the skipped nulls of {keys[1]} should be counted"


TEST_NAMES = [
    "test_the_script_removes_all_four_keys_and_every_record_then_updates_under_the_older_schema",
    "test_the_script_can_be_stopped_and_run_again",
]
