"""A record with and without the processing diagnostics, through the production graph providers.

Both stores: a record written before the fields existed and one carrying them are written and
updated by the provider (which validates against the strict record schema), read back with the
values intact, and left without them by `api_record`; a wrong-typed value is refused.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.common.utils import api_record
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("record-diagnostics-provider-it")
RECORDS = CollectionNames.RECORDS.value
DIAGNOSTICS = {"firstQueuedAt": 1_700_000_000_000, "failedAt": 1_700_000_100_000, "indexingAttempts": 2, "pageCount": 12}


def _record(key: str, org: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "id": key, "orgId": org, "recordName": key, "externalRecordId": key, "recordType": "FILE",
        "origin": "CONNECTOR", "connectorName": "KB", "connectorId": f"{org}-c", "version": 1,
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "indexingStatus": "NOT_STARTED", **extra,
    }


@pytest.fixture(params=["neo4j", "arango"])
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Any, str]]:
    try:
        provider = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "record_diagnostics_provider_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    org = f"rd-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org
    finally:
        for key in (f"{org}-old", f"{org}-new"):
            with contextlib.suppress(Exception):
                await provider.delete_nodes([key], RECORDS)
        with contextlib.suppress(Exception):
            await provider.disconnect()


async def test_old_and_new_records_write_update_and_read_back(store: tuple[Any, str]) -> None:
    provider, org = store
    old_key, new_key = f"{org}-old", f"{org}-new"
    assert await provider.batch_upsert_nodes([_record(old_key, org)], RECORDS)
    assert await provider.batch_upsert_nodes([_record(new_key, org, **DIAGNOSTICS)], RECORDS)

    # The record without the fields takes an ordinary update, and then the fields.
    assert await provider.update_node(old_key, RECORDS, {"indexingStatus": "QUEUED"})
    assert await provider.update_node(old_key, RECORDS, {"firstQueuedAt": 5, "indexingAttempts": 1})
    assert await provider.update_node(new_key, RECORDS, {"failedAt": 1_700_000_200_000, "indexingAttempts": 3})

    old = await provider.get_document(old_key, RECORDS)
    new = await provider.get_document(new_key, RECORDS)
    assert (old["firstQueuedAt"], old["indexingAttempts"]) == (5, 1)
    assert "pageCount" not in old or old["pageCount"] is None
    assert (new["firstQueuedAt"], new["failedAt"], new["indexingAttempts"], new["pageCount"]) == (
        DIAGNOSTICS["firstQueuedAt"], 1_700_000_200_000, 3, 12,
    )

    # What the record endpoint returns.
    for doc in (old, new):
        shown = api_record(doc)
        assert not set(DIAGNOSTICS) & set(shown)
        assert shown["recordName"] == doc["recordName"]


@pytest.mark.parametrize("bad", [{"pageCount": 0}, {"indexingAttempts": -1}, {"failedAt": "x"}, {"firstQueuedAt": "1"}, {"indexingAttempts": 2.0}, {"pageCount": 10**30}])
async def test_a_wrong_typed_diagnostic_is_refused(store: tuple[Any, str], bad: dict[str, Any]) -> None:
    provider, org = store
    key = f"{org}-old"
    assert await provider.batch_upsert_nodes([_record(key, org)], RECORDS)
    # Neo4j: the provider's validator raises. ArangoDB: the collection's schema answers 1620.
    try:
        refused = not await provider.update_node(key, RECORDS, bad)
    except Exception as error:
        refused = isinstance(error, SchemaValidationError) or "1620" in str(error)
    assert refused
    (name, value), = bad.items()
    assert (await provider.get_document(key, RECORDS)).get(name) != value
