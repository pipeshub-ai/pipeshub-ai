"""The widened records schema on a real ArangoDB.

Applies the schema as it was before the record processing diagnostics existed, stores
records under it, then runs ``ensure_schema`` (what every service does at startup) and
checks that:

* every record stored under the old schema still updates (the collection's level is
  strict, so each update re-validates the whole document);
* a record carrying the four diagnostics inserts and updates;
* a value of the wrong type is still refused (error 1620).

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_diagnostics_schema_arango.py -m integration
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.arango.documents import record_schema
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from tests.integration.graph_db.record_diagnostics_support import drop_database
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.arango_only, pytest.mark.timeout(300)]

logger = logging.getLogger("record-diagnostics-schema-it")
RECORDS = CollectionNames.RECORDS.value
FIELDS = ("firstQueuedAt", "failedAt", "indexingAttempts", "pageCount")
SCHEMA_VIOLATION = 1620

DIAGNOSTICS = {"firstQueuedAt": 1_700_000_000_000, "failedAt": 1_700_000_100_000, "indexingAttempts": 2, "pageCount": 12}


def _schema_before_diagnostics() -> dict[str, Any]:
    old = copy.deepcopy(record_schema)
    for name in FIELDS:
        del old["rule"]["properties"][name]
    # an image of that time knows no generation marker
    old["rule"].pop("$comment", None)
    return old


def _record(key: str, **extra: object) -> dict[str, Any]:
    return {
        "_key": key, "orgId": "org-1", "recordName": key, "externalRecordId": key, "recordType": "FILE",
        "origin": "CONNECTOR", "connectorId": "conn-1", "createdAtTimestamp": 1,
        "indexingStatus": "NOT_STARTED", **extra,
    }


class _Store:
    def __init__(self, provider) -> None:
        self.provider = provider
        self.client = provider.http_client

    async def _send(self, method: str, path: str, body: object = None) -> tuple[int, dict]:
        session = await self.client._get_session()
        url = f"{self.client.base_url}/_db/{self.client.database}/{path}"
        async with session.request(method, url, json=body) as resp:
            return resp.status, await resp.json()

    async def insert(self, doc: dict) -> int | None:
        status, body = await self._send("POST", f"_api/document/{RECORDS}", doc)
        return None if status < 300 else body["errorNum"]

    async def patch(self, key: str, fields: dict) -> int | None:
        status, body = await self._send("PATCH", f"_api/document/{RECORDS}/{key}", fields)
        return None if status < 300 else body["errorNum"]

    async def get(self, key: str) -> dict:
        return (await self._send("GET", f"_api/document/{RECORDS}/{key}"))[1]

    async def set_schema(self, schema: dict) -> None:
        assert await self.client.update_collection_schema(RECORDS, schema) is True


@pytest.fixture
async def store() -> AsyncIterator[_Store]:
    try:
        provider = await connect_arango(logger, f"rd_schema_{uuid.uuid4().hex[:8]}")
    except Exception as exc:
        backend_unavailable("arango", exc)
    try:
        yield _Store(provider)
    finally:
        # each case has a database of its own, with every collection: dropped, or a small server runs out of memory
        await drop_database(provider)
        await provider.disconnect()


async def test_old_records_and_records_with_diagnostics_both_write_under_the_widened_schema(store: _Store) -> None:
    # The store as it was before: strict, without the four properties.
    await store.set_schema(_schema_before_diagnostics())
    old_keys = [f"old-{i}" for i in range(5)]
    for key in old_keys:
        assert await store.insert(_record(key)) is None
    # The test sees the old schema: it refuses a record that carries a diagnostic.
    assert await store.insert(_record("control", **DIAGNOSTICS)) == SCHEMA_VIOLATION
    assert await store.patch(old_keys[0], {"pageCount": 3}) == SCHEMA_VIOLATION

    # What a service does at startup.
    assert await store.provider.ensure_schema() is True
    status, props = await store._send("GET", f"_api/collection/{RECORDS}/properties")
    assert props["schema"]["rule"] == record_schema["rule"]

    # Every pre-existing record still updates, with and without the new fields.
    for key in old_keys:
        assert await store.patch(key, {"indexingStatus": "QUEUED", "queuedAtTimestamp": 5}) is None
    assert await store.patch(old_keys[1], DIAGNOSTICS) is None
    assert {name: (await store.get(old_keys[1]))[name] for name in FIELDS} == DIAGNOSTICS

    # A record carrying the diagnostics inserts and updates; null is legal (Neo4j-style absent/null reads).
    assert await store.insert(_record("new-1", **DIAGNOSTICS)) is None
    assert await store.patch("new-1", {"indexingAttempts": 3, "failedAt": None}) is None
    assert await store.insert(_record("new-2", **dict.fromkeys(FIELDS))) is None
    assert await store.provider.update_node("new-2", RECORDS, {"firstQueuedAt": 9, "indexingAttempts": 0}) is True


@pytest.mark.parametrize(
    "bad",
    [
        {"firstQueuedAt": "1700000000000"},
        {"failedAt": "x"},
        {"indexingAttempts": "2"},
        {"indexingAttempts": -1},
        {"indexingAttempts": 1.5},
        {"pageCount": 0},
        {"pageCount": "3"},
        {"indexingAttempts": 2.0},
        {"pageCount": 1e20},
        {"indexingAttempts": -0.0},
        {"pageCount": 10**30},
    ],
)
async def test_a_wrong_typed_diagnostic_is_still_refused(store: _Store, bad: dict) -> None:
    await store.provider.ensure_schema()
    key = f"bad-{uuid.uuid4().hex[:6]}"
    assert await store.insert(_record(key)) is None
    assert await store.patch(key, bad) == SCHEMA_VIOLATION
    assert await store.insert(_record(f"{key}-2", **bad)) == SCHEMA_VIOLATION


async def test_an_old_process_narrowing_the_schema_again_does_not_break_records_without_the_fields(store: _Store) -> None:
    assert await store.provider.ensure_schema() is True
    keys = [f"r{i}" for i in range(20)]
    for key in keys:
        assert await store.insert(_record(key)) is None
    await store.set_schema(_schema_before_diagnostics())   # a process on the previous version starts
    for key in keys:
        assert await store.patch(key, {"indexingStatus": "QUEUED", "queuedAtTimestamp": 9}) is None
    assert await store.provider.ensure_schema() is True    # the new version starts again
    for key in keys:
        assert await store.patch(key, {"indexingStatus": "COMPLETED"}) is None


async def test_wide_and_narrow_schema_writers_running_together_never_fail_an_update(store: _Store) -> None:
    assert await store.provider.ensure_schema() is True
    keys = [f"c{i}" for i in range(10)]
    for key in keys:
        assert await store.insert(_record(key)) is None
    narrow, wide = _schema_before_diagnostics(), copy.deepcopy(record_schema)
    failures: list[int] = []

    async def flip(count: int) -> None:
        for i in range(count):
            await store.set_schema(narrow if i % 2 else wide)

    async def update(count: int) -> None:
        for i in range(count):
            error = await store.patch(keys[i % len(keys)], {"queuedAtTimestamp": i})
            if error is not None:
                failures.append(error)

    await asyncio.gather(flip(30), flip(30), update(200), update(200))
    assert failures == []


@pytest.mark.parametrize("name", FIELDS)
@pytest.mark.parametrize("value", [2.0, True, 1e20, 2**53 + 1, -0.0, {}, [], 10**30, 7, 0, 1, -1], ids=repr)
async def test_the_validator_the_other_graph_stores_use_agrees_with_this_schema(store: _Store, name: str, value: object) -> None:
    await store.provider.ensure_schema()
    key = f"e-{uuid.uuid4().hex[:8]}"
    assert await store.insert(_record(key)) is None
    arango_accepts = await store.patch(key, {name: value}) is None
    try:
        NodeSchemaValidator().validate_node_update(RECORDS, {name: value})
        python_accepts = True
    except SchemaValidationError:
        python_accepts = False
    assert python_accepts == arango_accepts, (name, value)
