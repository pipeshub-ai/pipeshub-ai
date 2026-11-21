"""The insert-only record fields on real ArangoDB and Neo4j.

  docker run -d --name g55-arango -p 18529:8529 -e ARANGO_ROOT_PASSWORD=ensure-it-pass arangodb:3.12.4
  docker run -d --name g55-neo4j -p 17687:7687 -e NEO4J_AUTH=neo4j/ensure-it-pass neo4j:5.26.0
  cd backend/python && pytest tests/integration/graph_db/test_record_insert_only_real_backends.py -m integration

The PostgreSQL leg is ``postgres/test_record_insert_only.py``; both run the cases in ``tests/support/record_insert_only.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support import record_insert_only as cases
from tests.support.record_insert_only_stores import ArangoStore, Neo4jStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
_logger = logging.getLogger("record-insert-only-it")


@pytest.fixture(params=["arangodb", "neo4j"])
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[cases.StoreAdapter]:
    try:
        if request.param == "arangodb":
            provider = await connect_arango(_logger, "record_insert_only_it")
        else:
            provider = await connect_neo4j(_logger, monkeypatch)
    except Exception as exc:
        backend_unavailable("arango" if request.param == "arangodb" else "neo4j", exc)
    adapter = (ArangoStore if request.param == "arangodb" else Neo4jStore)(provider)
    try:
        yield adapter
    finally:
        await provider.disconnect()


@pytest.fixture(params=[True, False], ids=["incarnation-on", "incarnation-off"])
def flag(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true" if request.param else "false")
    return request.param


@pytest.mark.parametrize("case", cases.ALL_CASES, ids=lambda c: c.__name__)
async def test_insert_only_cases(store: cases.StoreAdapter, flag: bool, case) -> None:
    await case(store, flag)


@pytest.mark.parametrize("hinted", [True, False], ids=["marked-promotion", "unmarked-promotion"])
async def test_stub_then_real(store: cases.StoreAdapter, flag: bool, hinted: bool) -> None:
    await cases.stub_then_real(store, flag, hinted=hinted)


async def test_with_the_kill_switch_off_no_store_mints(store: cases.StoreAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true")
    await cases.with_the_kill_switch_off_no_store_mints(store, True)


async def test_with_the_kill_switch_off_a_promotion_takes_the_incoming_date(store: cases.StoreAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true")
    await cases.with_the_kill_switch_off_a_promotion_takes_the_incoming_date(store, True)


async def test_a_restore_survives_a_same_id_write_with_the_kill_switch_off(store: cases.StoreAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true")
    await cases.a_restore_survives_a_same_id_write_with_the_kill_switch_off(store, True)
