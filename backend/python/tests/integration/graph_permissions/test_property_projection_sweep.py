"""The sweep's statements, index and state document against real Neo4j and ArangoDB.

The unit tests run the sweep against an in-memory graph whose selections are a Python reference of the
engines' statements. This module proves the statements agree with that reference, page by ``(time, id)``
without splitting ties, that the change index can be ensured (twice, beside an equivalent one) and queried once
online, and that the strict ``apps`` schema takes the state document and the epoch compare-and-set holds.
"""

import asyncio

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.indexing.property_projection_sweep import (
    GraphStateStore,
    Scope,
    SweepState,
)
from tests.unit.modules.indexing.sweep_world import stale
from tests.unit.modules.record_metadata.projection_world import kb_doc

from .write_path_support import _suffix

pytestmark = pytest.mark.integration

RECORDS = CollectionNames.RECORDS.value
APPS = CollectionNames.APPS.value
ORG = "org-it"
HASH, GEN = "0123456789abcdef", 2


@pytest.fixture(params=["arango", "neo4j"])
def provider(request):  # noqa: ANN201 - the provider under test; only the requested store's fixture is built (and skips alone if absent)
    return request.getfixturevalue(f"{request.param}_provider")


def _doc(connector: str, key: str, *, updated: int = 5_000, **fields) -> dict:
    return {**kb_doc(key, kb_id=connector, updated=updated), "orgId": ORG, **fields}


async def _ready_index(provider) -> None:
    await provider.ensure_record_change_index()
    for _ in range(120):
        if await provider.record_change_index_online():
            return
        await asyncio.sleep(1)
    raise AssertionError("the change index never came online")


_WORLDS = {
    "never": {},
    "current": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 5_000},
    "other_set": {"propsSet": "ffffffffffffffff", "propsGen": GEN, "propsSrc": 5_000},
    "older_gen": {"propsSet": HASH, "propsGen": GEN - 1, "propsSrc": 5_000},
    "newer_gen": {"propsSet": HASH, "propsGen": GEN + 1, "propsSrc": 5_000},
    "older_stamp": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 4_999},
    "newer_stamp": {"propsSet": HASH, "propsGen": GEN, "propsSrc": 5_001},
    "stamp_zero": {"updatedAtTimestamp": 0},
    "string_gen": {"propsSet": HASH, "propsGen": "2", "propsSrc": 5_000},
    "deleted": {"isDeleted": True},
}


async def test_both_selections_agree_with_the_reference(provider) -> None:
    """Kills: a statement that differs from the reference in what it calls stale (either engine, either selection)."""
    connector = f"c-{_suffix()}"
    # ArangoDB's record schema refuses a generation that is not a number, so a corrupt one can only be stored on the stores that do not validate it
    worlds = {n: e for n, e in _WORLDS.items() if not (n == "string_gen" and type(provider).__name__ == "ArangoHTTPProvider")}
    docs = {name: _doc(connector, f"{name}-{_suffix()}", **extra) for name, extra in worlds.items()}
    assert await provider.batch_upsert_nodes(list(docs.values()), RECORDS)
    await _ready_index(provider)
    expected = {d["_key"] for d in docs.values() if not d.get("isDeleted") and stale(d, HASH, GEN)}

    seek = await provider.get_records_updated_since(
        connector, ORG, since=0, after_id="", props_hash=HASH, props_gen=GEN, limit=100,
    )
    walk = await provider.get_records_needing_projection(
        connector, ORG, after_key=None, props_hash=HASH, props_gen=GEN, limit=100,
    )

    assert {r["_key"] for r in seek} == expected
    assert {r["_key"] for r in walk} == expected
    other_org = await provider.get_records_needing_projection(
        connector, "another-org", after_key=None, props_hash=HASH, props_gen=GEN, limit=100,
    )
    assert other_org == []


async def test_the_seek_pages_through_a_tie_without_splitting_it(provider) -> None:
    """3000 records with one write time, page 500. Kills: a keyset on the time alone, or a cursor without the id."""
    connector = f"tie-{_suffix()}"
    docs = [_doc(connector, f"t{i:04d}-{_suffix()}", updated=7_000) for i in range(3000)]
    for start in range(0, len(docs), 500):
        assert await provider.batch_upsert_nodes(docs[start:start + 500], RECORDS)
    await _ready_index(provider)
    seen, since, after = [], 0, ""
    while True:
        page = await provider.get_records_updated_since(
            connector, ORG, since=since, after_id=after, props_hash=HASH, props_gen=GEN, limit=500,
        )
        seen += [r["_key"] for r in page]
        if len(page) < 500:
            break
        since, after = page[-1]["updatedAtTimestamp"], page[-1]["_key"]
    assert sorted(seen) == sorted(d["_key"] for d in docs)


async def test_the_index_is_ensured_twice_and_then_online(provider) -> None:
    """Kills: a second index beside the one the scope listing makes; an 'equivalent index' error escaping;
    a state check that reads an index still building as online."""
    await provider.ensure_record_change_index()
    await provider.ensure_record_change_index()
    await _ready_index(provider)
    assert await provider.record_change_index_online()


async def test_the_state_document_fits_the_strict_apps_schema_and_the_epoch_is_a_compare_and_set(provider) -> None:
    key = f"app-{_suffix()}"
    assert await provider.batch_upsert_nodes([{
        "_key": key, "name": "KB", "type": "KB", "appGroup": "Collections", "scope": "team", "isActive": True,
        "createdAtTimestamp": 1, "orgId": ORG,
    }], APPS)
    store, scope = GraphStateStore(provider), Scope(key, ORG, __import__("app.config.constants.arangodb", fromlist=["Connectors"]).Connectors.KNOWLEDGE_BASE)
    results = await asyncio.gather(*(store.claim(scope, None) for _ in range(6)))
    assert sorted(r for r in results if r is not None) == [1], "exactly one of six claimants of epoch 1 wins"
    state = SweepState(watermark=5, covered_hash=HASH, covered_gen=2, lap="seek", cursor_ts=9, cursor_id="x")
    assert await store.save(scope, 1, state)
    assert not await store.save(scope, 7, SweepState(watermark=99))
    loaded, epoch = await store.load(scope)
    assert loaded == state and epoch == 1
