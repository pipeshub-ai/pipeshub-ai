"""The insert-only record fields (G-55) on the PostgreSQL graph provider (a migrated database: PG_CORE_URL)."""

from __future__ import annotations

import pytest

from tests.integration.graph_db.postgres.pg_support import new_org
from tests.support import record_insert_only as cases
from tests.support.record_insert_only_stores import PostgresStore

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.fixture
def store(pg_provider) -> cases.StoreAdapter:
    return PostgresStore(pg_provider, new_org())


@pytest.fixture(params=[True, False], ids=["incarnation-on", "incarnation-off"])
def flag(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true" if request.param else "false")
    return request.param


@pytest.mark.parametrize("case", cases.ALL_CASES, ids=lambda c: c.__name__)
async def test_insert_only_cases(store: cases.StoreAdapter, flag: bool, case) -> None:
    await case(store, flag)


async def test_stub_then_real(store: cases.StoreAdapter, flag: bool) -> None:
    await cases.stub_then_real(store, flag, hinted=False)      # PostgreSQL tests every row against the stored one


async def test_with_the_kill_switch_off_no_store_mints(store: cases.StoreAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true")
    await cases.with_the_kill_switch_off_no_store_mints(store, True)
