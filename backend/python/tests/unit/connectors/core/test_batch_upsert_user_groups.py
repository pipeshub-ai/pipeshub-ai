"""batch_upsert_user_groups writes one node per (connector, external id).

The groups it is given carry fresh ids, and the store writes by id, so a group
already stored must take that node's id; otherwise each sync adds a copy. The
real-database check is tests/integration/graph_db/test_servicenow_org_groups_real_backends.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import AppUserGroup


def _processor(stored: dict[str, str] | None = None) -> tuple[DataSourceEntitiesProcessor, AsyncMock]:
    """A processor whose store holds ``stored``: external id -> node id, for connector conn-1."""
    stored = stored or {}
    tx_store = AsyncMock()

    async def lookup(connector_id: str, external_id: str, *, raise_on_error: bool = False) -> AppUserGroup | None:
        if connector_id != "conn-1" or external_id not in stored:
            return None
        return AppUserGroup(
            id=stored[external_id], app_name=Connectors.SERVICENOW, connector_id=connector_id,
            source_user_group_id=external_id, name="old name",
        )

    tx_store.get_user_group_by_external_id = AsyncMock(side_effect=lookup)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    provider = MagicMock()
    provider.transaction.return_value = ctx
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, AsyncMock())
    proc.org_id = "org-1"
    return proc, tx_store


def _group(external_id: str, connector_id: str = "conn-1") -> AppUserGroup:
    return AppUserGroup(
        app_name=Connectors.SERVICENOW, connector_id=connector_id,
        source_user_group_id=external_id, name=f"DEPARTMENT_{external_id}",
    )


def _written(tx_store: AsyncMock) -> list[AppUserGroup]:
    tx_store.batch_upsert_user_groups.assert_awaited_once()
    return tx_store.batch_upsert_user_groups.await_args.args[0]


@pytest.mark.asyncio
async def test_a_stored_group_is_written_under_its_stored_id() -> None:
    proc, tx_store = _processor({"dept-1": "node-dept-1"})

    await proc.batch_upsert_user_groups([_group("dept-1")])

    written = _written(tx_store)
    assert [g.id for g in written] == ["node-dept-1"]
    assert written[0].name == "DEPARTMENT_dept-1"
    assert written[0].org_id == "org-1"
    tx_store.get_user_group_by_external_id.assert_awaited_once_with(
        connector_id="conn-1", external_id="dept-1", raise_on_error=True
    )


@pytest.mark.asyncio
async def test_a_new_group_keeps_its_fresh_id() -> None:
    proc, tx_store = _processor({"dept-1": "node-dept-1"})
    new = _group("dept-2")
    fresh_id = new.id

    await proc.batch_upsert_user_groups([_group("dept-1"), new])

    assert [g.id for g in _written(tx_store)] == ["node-dept-1", fresh_id]


@pytest.mark.asyncio
async def test_the_same_group_twice_in_one_batch_is_one_node() -> None:
    proc, tx_store = _processor()
    first, again = _group("dept-1"), _group("dept-1")

    await proc.batch_upsert_user_groups([first, again])

    assert again.id == first.id
    assert tx_store.get_user_group_by_external_id.await_count == 1


@pytest.mark.asyncio
async def test_the_lookup_is_per_connector() -> None:
    proc, tx_store = _processor({"dept-1": "node-dept-1"})
    other = _group("dept-1", connector_id="conn-2")
    fresh_id = other.id

    await proc.batch_upsert_user_groups([other])

    assert [g.id for g in _written(tx_store)] == [fresh_id]


@pytest.mark.asyncio
async def test_a_failed_lookup_writes_nothing() -> None:
    proc, tx_store = _processor()
    tx_store.get_user_group_by_external_id = AsyncMock(side_effect=RuntimeError("graph down"))

    with pytest.raises(RuntimeError, match="graph down"):
        await proc.batch_upsert_user_groups([_group("dept-1")])

    tx_store.batch_upsert_user_groups.assert_not_awaited()
