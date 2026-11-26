"""Noting the stored records of SQL tables a full sync did not write."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.sql.sync_lifecycle import note_tables_kept


def _setup(
    *, full_sync: bool, lookup_error: Exception | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    processor = MagicMock()
    processor.full_sync_running = MagicMock(return_value=full_sync)
    processor.get_record_by_external_id = AsyncMock(
        side_effect=lookup_error or (lambda **kw: SimpleNamespace(id=f"id:{kw['external_record_id']}")),
    )
    tx_store = MagicMock()
    tx_store.get_records_by_parent = AsyncMock(
        side_effect=lambda connector_id, fqn, record_type: [SimpleNamespace(id=f"id:{fqn}.c")],
    )

    @asynccontextmanager
    async def transaction() -> AsyncIterator[MagicMock]:
        yield tx_store

    provider = MagicMock()
    provider.transaction = transaction
    return processor, provider, tx_store


async def test_outside_a_full_sync_nothing_is_looked_up() -> None:
    processor, provider, tx_store = _setup(full_sync=False)
    assert await note_tables_kept(processor, provider, "c1", {"shop.a"}, logger=logging.getLogger("t"))
    processor.get_record_by_external_id.assert_not_called()
    tx_store.get_records_by_parent.assert_not_called()
    processor.note_kept_records.assert_not_called()


async def test_inside_a_full_sync_the_tables_and_their_columns_are_noted() -> None:
    processor, provider, _ = _setup(full_sync=True)
    assert await note_tables_kept(processor, provider, "c1", {"shop.b", "shop.a"}, logger=logging.getLogger("t"))
    processor.note_kept_records.assert_called_once_with(
        "c1", ["id:shop.a", "id:shop.a.c", "id:shop.b", "id:shop.b.c"],
    )


async def test_a_failed_lookup_is_logged_with_the_table(caplog) -> None:
    processor, provider, _ = _setup(full_sync=True, lookup_error=RuntimeError("graph down"))
    with caplog.at_level(logging.WARNING, logger="t"):
        assert not await note_tables_kept(processor, provider, "c1", {"shop.a"}, logger=logging.getLogger("t"))
    processor.note_kept_records.assert_not_called()
    assert "shop.a" in caplog.text
    assert "RuntimeError: graph down" in caplog.text
